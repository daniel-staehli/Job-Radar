"""Choose fetched vacancies for an explicit Ollama match review."""
from copy import deepcopy
import queue
import tkinter as tk
from tkinter import ttk

from . import discovery_match, job_match, work_rights
from .cancellation import CancellationToken, cancellation_scope
from .providers import describe


class DiscoveryMatchView:
    def show_candidate_match(self):
        try:
            candidate = self._candidate()
            review = discovery_match.saved_reviews(self.service.root, [candidate]).get(candidate['id'])
            if not review:
                raise ValueError('This vacancy has not been reviewed. Choose Review with Ollama first.')
            if review.get('error'):
                raise ValueError(review['error'])
            self._show_text('Saved vacancy match review', job_match.format_assessment(review))
        except (ValueError, OSError) as exc:
            self._error(exc)

    def open_discovery_review(self):
        if self.busy:
            return
        existing = getattr(self, '_discovery_review_dialog', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        try:
            if self._profile_has_unsaved_changes():
                raise ValueError('Save your changes in My profile before reviewing vacancies.')
            settings = discovery_match.local_settings(self._selected_ai())
            visible = self.candidate_tree.get_children()
            lookup = {item['id']: item for item in self.candidates}
            profile, _ = work_rights.load(self.service.root)
            candidates = deepcopy([lookup[key] for key in visible if not lookup[key].get('stale')
                                   and work_rights.assess(lookup[key], profile).status != work_rights.EXCLUDED])
            if not candidates:
                raise ValueError('No visible vacancies are available for review. Refresh the feed or adjust the filters. Confirmed ineligible and stale listings are skipped.')
            job_match._profile_snapshot(self.service.root)
        except (ValueError, OSError) as exc:
            return self._error(exc)
        dialog = tk.Toplevel(self.window)
        self._discovery_review_dialog = dialog
        dialog.title('Review vacancies with Ollama')
        dialog.geometry('850x600')
        dialog.minsize(720, 560)
        dialog.transient(self.window)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Choose up to 10 vacancies', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Ctrl-click to select several. Vacancies follow the shortlist order. Confirmed ineligible and stale listings are skipped.',
                  wraplength=760).pack(anchor='w', pady=(6, 8))
        ttk.Label(frame, text='Uses ' + describe(settings) + '\nSends the selected vacancies and relevant saved profile facts to your local Ollama server. No OpenAI request is made.',
                  wraplength=760, style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        footer = ttk.Frame(frame)
        footer.pack(side='bottom', fill='x')
        listing = ttk.Frame(frame)
        listing.pack(fill='both', expand=True)
        tree = ttk.Treeview(listing, columns=('company', 'title', 'match'), show='headings', selectmode='extended')
        for key, label, width in [('company', 'Employer', 150), ('title', 'Position', 340), ('match', 'Saved match', 120)]:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=80)
        scrollbar = ttk.Scrollbar(listing, orient='vertical', command=tree.yview)
        scrollbar.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(fill='both', expand=True)
        reviews = discovery_match.saved_reviews(self.service.root, candidates)
        for item in candidates:
            tree.insert('', 'end', iid=item['id'], values=(item['company'], item['title'], discovery_match.label(reviews.get(item['id']))))
        selected = self.candidate_tree.selection()
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
        else:
            tree.selection_set(candidates[0]['id'])
        reuse = tk.BooleanVar(value=True)
        ttk.Checkbutton(footer, text='Reuse current reviews from the same model and settings', variable=reuse).pack(anchor='w', pady=(10, 6))
        notice = tk.StringVar(value='One model request per vacancy that needs a review; this may take several minutes each. Completed reviews are kept if you stop.')
        ttk.Label(footer, textvariable=notice, wraplength=760).pack(anchor='w', pady=(0, 10))
        actions = ttk.Frame(footer)
        actions.pack(fill='x')
        progress = queue.Queue()
        state = {'running': False, 'token': None, 'timer': None}

        def poll():
            state['timer'] = None
            if not dialog.winfo_exists():
                return
            if state['token'] is None or not state['token'].is_cancelled:
                try:
                    while True:
                        notice.set(progress.get_nowait())
                except queue.Empty:
                    pass
            if state['running']:
                state['timer'] = dialog.after(200, poll)

        def stop():
            if state['running']:
                state['token'].cancel()
                stop_button.configure(state='disabled')
                notice.set('Stopping the current request. Completed reviews will remain available.')

        def complete(message):
            state['running'] = False
            if state['timer'] is not None:
                dialog.after_cancel(state['timer'])
                state['timer'] = None
            self.discovery_ai_sort.set(True)
            self._fill_candidates()
            self.discovery_status.set(message)
            self.status.set(message)
            if dialog.winfo_exists():
                notice.set(message)
                start_button.configure(state='normal')
                stop_button.configure(state='disabled')
                refreshed = discovery_match.saved_reviews(self.service.root, candidates)
                for item in candidates:
                    tree.set(item['id'], 'match', discovery_match.label(refreshed.get(item['id'])))

        def start():
            if self.busy or state['running']:
                return
            ids = set(tree.selection())
            if not 1 <= len(ids) <= discovery_match.MAX_BATCH:
                notice.set('Select between 1 and 10 vacancies. No request was sent.')
                return
            if self._profile_has_unsaved_changes():
                notice.set('Save your profile changes before starting. No request was sent.')
                return
            batch = [item for item in candidates if item['id'] in ids]
            reuse_saved = reuse.get()
            token = CancellationToken()
            state.update(running=True, token=token)
            start_button.configure(state='disabled')
            stop_button.configure(state='normal')
            notice.set(f'Preparing {len(batch)} selected vacancies…')

            def action():
                with cancellation_scope(token):
                    return discovery_match.review_candidates(self.service.root, batch, settings,
                        reuse=reuse_saved, progress=progress.put)

            def done(result):
                message = ('Review stopped. ' if result['cancelled'] else 'Review finished. ')
                message += f"{len(result['reviewed'])} new reviews; {len(result['reused'])} reused."
                if result['errors']:
                    error = result['errors'][0]
                    message += f" Stopped at {error['title']}: {error['message']}"
                complete(message)

            self._run('Reviewing fetched vacancies with Ollama…', action, done,
                      lambda error: complete('Review stopped. Completed reviews remain saved. ' + str(error)))
            poll()

        def close():
            if state['running']:
                stop()
            else:
                dialog.destroy()

        start_button = ttk.Button(actions, text='Review selected vacancies', command=start, style='Primary.TButton')
        start_button.pack(side='left', padx=(0, 8))
        stop_button = ttk.Button(actions, text='Stop review', command=stop, state='disabled')
        stop_button.pack(side='left')
        ttk.Button(actions, text='Close', command=close).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW', close)

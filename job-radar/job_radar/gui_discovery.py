"""Review employer-feed results before adding them to the application tracker."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlsplit
from .discovery import suggested_presets
from .ranking import load_terms, parse_terms, save_terms, term_breakdown


class DiscoveryView:
    def _build_discovery(self):
        self.presets, self.candidates = [], []
        self.priority_groups = []
        ttk.Label(self.find_tab, text="Find roles at employers you choose", style="Heading.TLabel").pack(anchor='w')
        ttk.Label(self.find_tab, text="Follow selected employers on Ashby or Greenhouse. Add employers first, then refresh their vacancy lists. Other websites: use Add a job.",
                  style="Muted.TLabel", wraplength=1000).pack(anchor='w', pady=(4, 10))
        controls = ttk.Frame(self.find_tab)
        controls.pack(fill='x')
        self.employer_choice = ttk.Combobox(controls, state='readonly', width=38)
        self.employer_choice.pack(side='left', padx=(0, 10))
        self.employer_choice.bind('<<ComboboxSelected>>', lambda _: self._fill_candidates())
        self._button(controls, 'Refresh vacancies', self.refresh_discovery, primary=True)
        self._button(controls, 'Manage employers', self.manage_employers)
        self.discovery_status = tk.StringVar(value='Add an employer using Manage employers. Ashby and Greenhouse boards are supported.')
        ttk.Label(self.find_tab, textvariable=self.discovery_status, style='Muted.TLabel', wraplength=1000).pack(anchor='w', pady=(10, 6))
        filters = ttk.Frame(self.find_tab)
        filters.pack(fill='x', pady=(0, 8))
        ttk.Label(filters, text='Filter results').pack(side='left', padx=(0, 8))
        self.discovery_query = tk.StringVar()
        ttk.Entry(filters, textvariable=self.discovery_query).pack(side='left', fill='x', expand=True)
        self.discovery_query.trace_add('write', lambda *_: self._fill_candidates())
        priority = ttk.Frame(self.find_tab)
        priority.pack(fill='x', pady=(0, 8))
        ttk.Label(priority, text='Priority terms').pack(side='left', padx=(0, 8))
        self.priority_terms = tk.StringVar(value='')
        ttk.Entry(priority, textvariable=self.priority_terms).pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._button(priority, 'Rank by terms (local)', self.rank_candidates)
        panels = ttk.Panedwindow(self.find_tab, orient='horizontal')
        panels.pack(fill='both', expand=True)
        listing, details = ttk.Frame(panels), ttk.Frame(panels, padding=(12, 0, 0, 0))
        panels.add(listing, weight=3)
        panels.add(details, weight=2)
        self.candidate_tree = ttk.Treeview(listing, columns=('company', 'title', 'location', 'terms'), show='headings', selectmode='browse')
        for key, label, width in [('company', 'Employer', 110), ('title', 'Position', 220), ('location', 'Location', 125), ('terms', 'Terms', 70)]:
            self.candidate_tree.heading(key, text=label)
            self.candidate_tree.column(key, width=width, minwidth=70)
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.candidate_tree.pack(fill='both', expand=True)
        self.discovery_empty = ttk.Label(listing, text='', justify='center', anchor='center',
                                         wraplength=390, style='Muted.TLabel')
        self.candidate_tree.tag_configure('alternate', background='#f5f8fa')
        self.candidate_tree.bind('<<TreeviewSelect>>', lambda _: self._show_candidate())
        self.candidate_viewer = ScrolledText(details, width=32, height=10, wrap='word', padx=12, pady=10)
        self.candidate_viewer.pack(fill='both', expand=True)
        self.candidate_viewer.configure(state='disabled')
        actions = ttk.Frame(self.find_tab)
        actions.pack(side='bottom', fill='x', pady=(12, 0), before=panels)
        self._button(actions, 'Save selected job to tracker', self.save_candidate, primary=True)
        self._button(actions, 'Review in manual form', self.edit_candidate)
        ttk.Label(self.find_tab, text='Priority example: microscopy; R | Python; Lausanne. Each group counts once. Matches show words mentioned in the vacancy, not a prediction of your suitability.',
                  style='Muted.TLabel', wraplength=1000).pack(side='bottom', anchor='w', pady=(8, 0), before=actions)
        self._load_discovery()

    def _load_discovery(self):
        # Reading cached settings/results never contacts an employer.
        try:
            previous = self.employer_choice.get()
            self.presets = self.service.employer_presets()
            self.candidates = self.service.discovery_candidates()
            expression = load_terms(self.service.root)
            self.priority_terms.set(expression)
            self.priority_groups = parse_terms(expression)
            labels = ['All saved employers'] + [f"{p['company']} ({p['provider']})" for p in self.presets]
            self.employer_choice.configure(values=labels)
            self.employer_choice.current(labels.index(previous) if previous in labels else 0)
            self._fill_candidates()
            if self.candidates:
                newest = max(str(c.get('fetched_at', '')) for c in self.candidates)
                self.discovery_status.set(f"{len(self.candidates)} cached vacancies · Last successful fetch: {newest}. Refresh for current listings.")
            elif self.presets:
                self.discovery_status.set(f"{len(self.presets)} employers saved. Refresh vacancies to check their published roles.")
            else:
                self.discovery_status.set('No employers added yet. Start with Manage employers; nothing is fetched automatically.')
        except Exception as exc:
            self._error(exc)

    def _fill_candidates(self):
        selected = self.candidate_tree.selection()
        query = self.discovery_query.get().strip().casefold()
        employer_index = self.employer_choice.current()
        preset_id = self.presets[employer_index - 1]['id'] if 0 < employer_index <= len(self.presets) else None
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        ordered = sorted(self.candidates, key=lambda item: -term_breakdown(item, self.priority_groups)['count']) if self.priority_groups else self.candidates
        for item in ordered:
            if preset_id and item.get('preset_id') != preset_id:
                continue
            if query and query not in ' '.join(str(item.get(k, '')) for k in ('company', 'title', 'location', 'work_mode')).casefold():
                continue
            tag = 'alternate' if len(self.candidate_tree.get_children()) % 2 else ''
            breakdown = term_breakdown(item, self.priority_groups)
            terms = f"{breakdown['count']}/{breakdown['total']}" if breakdown['total'] else ''
            self.candidate_tree.insert('', 'end', iid=item['id'], values=(item['company'], item['title'], item['location'], terms), tags=(tag,))
        if self.candidate_tree.get_children():
            self.discovery_empty.place_forget()
        else:
            message = ('No employers added yet\n\n1. Choose Manage employers.\n2. Add an employer\'s Ashby or Greenhouse link.\n3. Click Refresh vacancies.' if not self.presets
                       else 'No vacancies match this filter.\n\nClear Filter results or choose All saved employers.' if self.candidates
                       else 'No vacancies fetched yet\n\nClick Refresh vacancies to check your saved employers.\nA board may also have no published roles.')
            self.discovery_empty.configure(text=message)
            self.discovery_empty.place(relx=0.5, rely=0.45, anchor='center', relwidth=0.95)
        if selected and self.candidate_tree.exists(selected[0]):
            self.candidate_tree.selection_set(selected[0])
        self._show_candidate()

    def _candidate(self):
        selected = self.candidate_tree.selection()
        if not selected:
            raise ValueError('Select one vacancy from the results first.')
        return next(item for item in self.candidates if item['id'] == selected[0])

    def _show_candidate(self):
        try:
            item = self._candidate()
            notice = '\nCached result: the last refresh failed. Check the source before applying.\n' if item.get('stale') else ''
            missing = '\nMissing details: ' + ', '.join(item['missing_fields']) + '. Use Review in manual form.\n' if item.get('missing_fields') else ''
            breakdown = term_breakdown(item, self.priority_groups)
            priority = ''
            if breakdown['total']:
                matched = '; '.join(group['group'] for group in breakdown['matched']) or 'None'
                absent = '; '.join(breakdown['missing']) or 'None'
                priority = f"\nPriority groups mentioned: {breakdown['count']}/{breakdown['total']}\nMatched: {matched}\nNot mentioned: {absent}\n"
            text = (f"{item['title']}\n{item['company']} · {item['location']}\n\n"
                    f"Source: {item['provider']}\n{item['source_url']}\n"
                    f"Fetched: {item.get('fetched_at', '')}\n{notice}{missing}{priority}\n{item.get('description', '')}")
        except (ValueError, StopIteration):
            text = ('Select a vacancy to read its full description.\n\nNothing is added to your tracker until you choose Save selected job.'
                    if self.candidates else 'Your employer vacancies will appear here.\n\n1. Add a board in Manage employers.\n2. Refresh vacancies.\n3. Review and save the roles you want.')
        self.candidate_viewer.configure(state='normal')
        self.candidate_viewer.delete('1.0', 'end')
        self.candidate_viewer.insert('1.0', text)
        self.candidate_viewer.configure(state='disabled')

    def rank_candidates(self):
        try:
            self.priority_groups = save_terms(self.service.root, self.priority_terms.get())
            self._fill_candidates()
            self.status.set('Priority terms saved. Results ranked locally; no model was called.')
        except (OSError, ValueError) as exc:
            self._error(exc)

    def refresh_discovery(self):
        if not self.presets:
            self.discovery_status.set('Add your first employer below, then choose Refresh vacancies.')
            return self.manage_employers()
        index = self.employer_choice.current()
        ids = [self.presets[index - 1]['id']] if index > 0 else None
        def done(result):
            self.candidates = result['candidates']
            self._fill_candidates()
            messages = [f"{e.get('company', 'Employer')}: {e['message']}" for e in result.get('errors', [])]
            self.discovery_status.set(f"{len(self.candidates)} vacancies available. " + (' | '.join(messages) if messages else 'Select a result to review it.'))
            self.status.set('Employer refresh finished. Your application tracker is unchanged.')
        self._run('Checking published employer vacancies…', lambda: self.service.refresh_employers(ids), done)

    def save_candidate(self):
        try:
            item = dict(self._candidate())
        except (ValueError, StopIteration) as exc:
            return self._error(exc)
        def done(result):
            job_id, action = result
            self.status_filter.set('All statuses')
            self.search.set('')
            self.refresh()
            self.job_tree.selection_set(job_id)
            self._job_selected()
            self.tabs.select(self.jobs_tab)
            self.status.set(f"Job saved ({action}). You can track progress or create documents.")
        self._run('Saving the selected vacancy…', lambda: self.service.save_discovered_job(item), done)

    def edit_candidate(self):
        try:
            item = self._candidate()
            fields = ('company', 'title', 'location', 'description', 'source_url', 'apply_url', 'work_mode', 'employment_type', 'posted_date')
            self._fill_draft({key: item.get(key) for key in fields})
            # Keep the source identity when a feed vacancy is completed/edited.
            self.discovery_draft = dict(item)
            self.import_url.set(item['source_url'])
            self.import_note.set('Review the vacancy details. Saving retains its employer-board identity.')
            self.tabs.select(self.import_tab)
        except (ValueError, StopIteration) as exc:
            self._error(exc)

    def manage_employers(self):
        window = tk.Toplevel(self.window)
        window.title('Employer presets')
        window.geometry('780x560')
        window.transient(self.window)
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Choose the employers you follow', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Add an Ashby or Greenhouse board URL, or its board name. Example: jobs.ashbyhq.com/company-name',
                  wraplength=710, style='Muted.TLabel').pack(anchor='w', pady=(5, 10))
        form = ttk.Frame(frame)
        form.pack(fill='x')
        company = self._field(form, 'Employer', 0)
        provider = self._field(form, 'Job board', 1, ('ashby', 'greenhouse'))
        provider.set('ashby')
        board = self._field(form, 'Board URL or name', 2)
        suggestions = suggested_presets(self.service.jobs())
        saved_ids = {item['id'] for item in self.service.employer_presets()}
        suggestions = [item for item in suggestions if item['id'] not in saved_ids]
        if suggestions:
            ttk.Label(frame, text='Found in your saved jobs: choose one to fill the form, then Save employer.',
                      style='Muted.TLabel', wraplength=710).pack(anchor='w', pady=(10, 4))
            choice = ttk.Combobox(frame, state='readonly', values=[f"{p['company']} ({p['provider']}: {p['board']})" for p in suggestions])
            choice.pack(fill='x')
            def choose_saved(_):
                index = choice.current()
                if index >= 0:
                    item = suggestions[index]
                    company.set(item['company'])
                    provider.set(item['provider'])
                    board.set(item['board'])
            choice.bind('<<ComboboxSelected>>', choose_saved)
        def detect_board(*_):
            try:
                host = urlsplit(board.get()).hostname
            except ValueError:
                return
            if host == 'jobs.ashbyhq.com':
                provider.set('ashby')
            elif host in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
                provider.set('greenhouse')
        board.trace_add('write', detect_board)
        tree = ttk.Treeview(frame, columns=('company', 'provider', 'board'), show='headings', selectmode='browse', height=5)
        for key, title in [('company', 'Employer'), ('provider', 'Job board'), ('board', 'Board name')]:
            tree.heading(key, text=title)
            tree.column(key, width=210)
        tree.pack(fill='both', expand=True, pady=(12, 10))
        def reload():
            tree.delete(*tree.get_children())
            for item in self.service.employer_presets():
                tree.insert('', 'end', iid=item['id'], values=(item['company'], item['provider'], item['board']))
            self._load_discovery()
        def save():
            try:
                self.service.save_employer({'company': company.get(), 'provider': provider.get(), 'board': board.get()})
                company.set('')
                board.set('')
                reload()
            except Exception as exc:
                self._error(exc)
        def remove():
            try:
                selected = tree.selection()
                if not selected:
                    raise ValueError('Select an employer to remove from the preset list.')
                self.service.remove_employer(selected[0])
                reload()
            except Exception as exc:
                self._error(exc)
        buttons = ttk.Frame(frame)
        buttons.pack(side='bottom', fill='x', before=tree)
        self._button(buttons, 'Save employer', save, primary=True)
        self._button(buttons, 'Remove selected preset', remove)
        self._button(buttons, 'Done', window.destroy, side='right')
        ttk.Label(frame, text='Removing a preset keeps your tracked applications and documents.', style='Muted.TLabel').pack(side='bottom', anchor='w', pady=(8, 0), before=buttons)
        reload()

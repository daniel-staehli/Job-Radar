"""Explicit provider settings and a visible destination for document requests."""
import tkinter as tk
from tkinter import ttk
import os
import queue
import threading

from .providers import (PROVIDER_LABELS, OLLAMA_CONTEXT_CHOICES, OLLAMA_CONTEXT_DEFAULT,
                        default_settings, describe, settings_check, normalize_settings, ollama_thinking_options)
from .model_catalog import EFFORTS, read_catalog, resolve_selection


class CodexModelControls(ttk.Frame):
    """A local model picker with reasoning choices tied to the selected model."""
    CUSTOM = 'Custom model (not verified)'

    def __init__(self, parent, current, *, catalog=None):
        super().__init__(parent)
        self.catalog = read_catalog() if catalog is None else catalog
        self.known = {item['model']: item for item in self.catalog['models']}
        self.default_model = os.getenv('JOB_RADAR_CODEX_MODEL', '').strip() or self.catalog['configured_model']
        self.default_label = 'Configured default · ' + (self.default_model or 'not known locally')
        self.model = tk.StringVar()
        self.custom = tk.StringVar(value=current.get('model', ''))
        self.effort = tk.StringVar()
        self.detail = tk.StringVar()
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text='Codex model').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=4)
        self.model_choice = ttk.Combobox(self, textvariable=self.model, state='readonly',
                                        values=[self.default_label, *self.known, self.CUSTOM])
        self.model_choice.grid(row=0, column=1, sticky='ew', pady=4)
        self.custom_label = ttk.Label(self, text='Exact custom model ID')
        self.custom_label.grid(row=1, column=0, sticky='w', padx=(0, 10), pady=4)
        self.custom_entry = ttk.Entry(self, textvariable=self.custom)
        self.custom_entry.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(self, text='Reasoning level').grid(row=2, column=0, sticky='w', padx=(0, 10), pady=4)
        self.effort_choice = ttk.Combobox(self, textvariable=self.effort, state='readonly')
        self.effort_choice.grid(row=2, column=1, sticky='ew', pady=4)
        ttk.Label(self, textvariable=self.detail, wraplength=700, style='Muted.TLabel').grid(
            row=3, column=0, columnspan=2, sticky='w', pady=(8, 5))
        selected = current.get('model', '')
        self.model.set(self.CUSTOM if selected and (current.get('custom_model') or selected not in self.known)
                       else selected or self.default_label)
        self._model_changed()
        saved_effort = current.get('reasoning_effort', '')
        if saved_effort in self.effort_choice['values']:
            self.effort.set(saved_effort)
        self.model.trace_add('write', lambda *_: self._model_changed())
        self.custom.trace_add('write', lambda *_: self._model_changed())
        self.effort.trace_add('write', lambda *_: self._describe())
        self._describe()

    def _model_changed(self):
        custom = self.model.get() == self.CUSTOM
        for widget in (self.custom_label, self.custom_entry):
            widget.grid() if custom else widget.grid_remove()
        model = self.custom.get().strip() if custom else (self.default_model if self.model.get() == self.default_label else self.model.get())
        levels = self.known.get(model, {}).get('efforts', list(EFFORTS) if custom else [])
        settings = {'model': '' if self.model.get() == self.default_label else model,
                    'custom_model': custom, 'reasoning_effort': ''}
        try:
            resolved = resolve_selection(settings, catalog=self.catalog)
            default = resolved['reasoning_effort'] or 'model decides'
        except ValueError:
            default = 'select a level below'
        self.default_effort_label = 'Default · ' + default
        values = [self.default_effort_label, *levels]
        previous = self.effort.get()
        self.effort_choice.configure(values=values)
        if previous not in values:
            self.effort.set(self.default_effort_label)
        self._describe()

    def values(self):
        selection = self.model.get()
        if selection not in self.model_choice['values']:
            raise ValueError('Choose a Codex model from the list.')
        if self.effort.get() not in self.effort_choice['values']:
            raise ValueError('Choose a reasoning level supported by this model.')
        custom = selection == self.CUSTOM
        model = self.custom.get().strip() if custom else ('' if selection == self.default_label else selection)
        if custom and not model:
            raise ValueError('Enter an exact custom model ID or choose a listed model.')
        return {'model': model, 'custom_model': custom,
                'reasoning_effort': '' if self.effort.get() == self.default_effort_label else self.effort.get()}

    def _describe(self):
        try:
            values = self.values()
            selected = resolve_selection(values, catalog=self.catalog)
            text = f"Will use {selected['model'] or 'an unknown default'} · reasoning: {selected['reasoning_effort'] or 'model default'}. "
            text += ('Custom IDs and their access are not verified. A typo is sent exactly and can fail; no automatic fallback. '
                     if values['custom_model'] else 'The list comes from Codex on this computer; account access is not tested. ')
            text += 'Higher reasoning can take longer and use more allowance; it is not a percentage of capacity.'
            if not self.known:
                text += ' No cached list was found. Open Codex, sign in, then reopen these settings.'
            self.detail.set(text)
        except ValueError as exc:
            self.detail.set(str(exc))


class OllamaThinkingControls(ttk.Frame):
    """Discover model-specific thinking options without sending any documents."""
    DEFAULT = 'Model default'

    def __init__(self, parent, settings_fn, current):
        super().__init__(parent)
        self.settings_fn = settings_fn
        self.choice = tk.StringVar(value=self.DEFAULT)
        self.detail = tk.StringVar()
        self.options = {self.DEFAULT: None}
        self._results = queue.Queue()
        self._busy = False
        self._revision = 0
        self._poll_id = None
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text='Ollama thinking').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=4)
        self.selector = ttk.Combobox(self, textvariable=self.choice, state='readonly')
        self.selector.grid(row=0, column=1, sticky='ew', pady=4)
        self.load_button = ttk.Button(self, text='Load thinking options', command=self.load_options)
        self.load_button.grid(row=1, column=1, sticky='w', pady=4)
        ttk.Label(self, textvariable=self.detail, wraplength=700, style='Muted.TLabel').grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        saved = current.get('ollama_thinking')
        if saved is not None:
            label = 'Saved: ' + self._label(saved) + ' (not checked)'
            self.options[label] = saved
            self.choice.set(label)
        self.selector.configure(values=list(self.options))
        self.detail.set('Load options from the local server for this model. Only its name is sent; no CV or job text. Saved choices are checked again before generation.')
        self.bind('<Destroy>', self._destroyed, add='+')

    @staticmethod
    def _label(value):
        if value is True:
            return 'On'
        if value is False:
            return 'Off'
        return 'Level: ' + value

    def value(self):
        if self.choice.get() not in self.options:
            raise ValueError('Choose an Ollama thinking option from the list.')
        return self.options[self.choice.get()]

    def inputs_changed(self, *_):
        self._revision += 1
        self.options = {self.DEFAULT: None}
        self.selector.configure(values=list(self.options))
        self.choice.set(self.DEFAULT)
        self.detail.set('Model or connection changed. Load its thinking options; Model default sends no override.')

    def load_options(self):
        if self._busy:
            return
        try:
            settings = normalize_settings(self.settings_fn(), require_model=True)
            if settings['provider'] != 'ollama':
                raise ValueError('Choose Ollama (local) first.')
        except (ValueError, OSError) as exc:
            self.detail.set(str(exc))
            return
        revision = self._revision
        self._busy = True
        self.load_button.configure(state='disabled')
        self.detail.set('Checking this model on your local Ollama server… No documents are sent.')
        results = self._results  # The worker must not retain Tk widgets after a dialog closes.
        def worker():
            try:
                results.put((revision, ollama_thinking_options(settings), None))
            except (ValueError, OSError, RuntimeError) as exc:
                results.put((revision, None, str(exc)))
            except Exception:
                results.put((revision, None, 'Could not read Ollama thinking options. Check that Ollama is running.'))
        threading.Thread(target=worker, daemon=True).start()
        self._poll_id = self.after(50, self._poll_options)

    def _poll_options(self):
        self._poll_id = None
        try:
            revision, result, error = self._results.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(50, self._poll_options)
            return
        self._busy = False
        self.load_button.configure(state='normal')
        if revision != self._revision:
            return  # Do not attach an earlier model's response to a new selection.
        if error:
            self.detail.set(error)
            return
        previous = self.value()
        self.options = {self.DEFAULT: None}
        for value in result['values']:
            self.options[self._label(value)] = value
        self.selector.configure(values=list(self.options))
        selected = next((label for label, value in self.options.items()
                         if type(value) is type(previous) and value == previous), self.DEFAULT)
        self.choice.set(selected)
        message = result['detail']
        default = result.get('default')
        if default is not None:
            message += ' Server default: ' + self._label(default) + '.'
        if previous is not None and selected == self.DEFAULT:
            message += ' The saved choice is not supported here; Model default is now selected. Save to keep this change.'
        self.detail.set(message)

    def _destroyed(self, event):
        if event.widget is self and self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None


class OllamaContextControls(ttk.Frame):
    """Choose per-request context capacity locally, independently of thinking."""
    def __init__(self, parent, current):
        super().__init__(parent)
        self.options = {f'{value:,} tokens': value for value in OLLAMA_CONTEXT_CHOICES}
        saved = current.get('ollama_context_tokens', OLLAMA_CONTEXT_DEFAULT)
        selected = next((label for label, value in self.options.items() if value == saved),
                        f'{OLLAMA_CONTEXT_DEFAULT:,} tokens')
        self.choice = tk.StringVar(value=selected)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text='Ollama context window').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=4)
        self.selector = ttk.Combobox(self, textvariable=self.choice, state='readonly', values=list(self.options))
        self.selector.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Label(self, text='Space for the job, profile, references, thinking and answer together. '
                  '32,768 tokens is the Job Radar default. Larger windows need more memory and may run more slowly.',
                  wraplength=700, style='Muted.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

    def value(self):
        if self.choice.get() not in self.options:
            raise ValueError('Choose an Ollama context window from the list.')
        return self.options[self.choice.get()]


class AiSettingsView:
    def _update_ai_labels(self):
        try:
            self.ai_current = self.service.ai_settings()
            label = describe(self.ai_current)
            self.ai_summary.set('Selected AI: ' + label)
            self.ai_disclosure.set('Review information stays local. Generate drafts sends the saved job, verified profile, selected reference text and included job-specific sources to ' + label + '. It does not submit an application.')
            self.ai_revision_disclosure.set('Improve sends your feedback and the saved application context to ' + label + '.')
            self.import_ai_notice.set('Optional AI extraction sends vacancy text and filled fields to ' + label +
                                      '. It reads the description only; links are not opened. Provider usage applies. Review before saving.')
            self.connection_status.set('Use Check setup to check this selection. Reading and reviewing stay local.')
        except (OSError, ValueError) as exc:
            self.ai_current = None
            self.ai_summary.set('AI settings need attention. Open AI settings before generating.')
            self.import_ai_notice.set('AI settings need attention before AI extraction. Fetching and local extraction still work without AI.')
            self.connection_status.set(str(exc))

    def _selected_ai(self):
        if self.ai_current is None:
            raise ValueError('Open AI settings and save a valid provider selection first.')
        return dict(self.ai_current)

    def _check_provider(self, settings):
        settings = normalize_settings(settings, require_model=True)
        if settings['provider'] == 'codex':
            result = self.service.check_codex()
            return {**result, 'ready': bool(result.get('installed') and result.get('authenticated')),
                    'detail': describe(settings) + '. ' + self._connection_text(result) + ' Model access has not been tested.'}
        return settings_check(settings)

    def _require_provider(self, settings):
        result = self._check_provider(settings)
        if not result.get('ready'):
            raise RuntimeError(result.get('detail') or 'Check AI settings before generating.')
        return result

    def open_ai_settings(self):
        window = tk.Toplevel(self.window)
        window.title('AI settings')
        window.geometry('820x720')
        window.minsize(760, 580)
        window.transient(self.window)
        body = ttk.Frame(window)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas, padding=20)
        content = canvas.create_window((0, 0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content, width=event.width))
        ttk.Label(frame, text='Choose how to create your documents', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Saving settings does not send documents or call a model.', style='Muted.TLabel').pack(anchor='w', pady=(5, 14))
        form = ttk.Frame(frame)
        form.pack(fill='x')
        keys = list(PROVIDER_LABELS)
        provider = self._field(form, 'Provider', 0, list(PROVIDER_LABELS.values()))
        current = self.ai_current or default_settings()
        codex_controls = CodexModelControls(form, current if current['provider'] == 'codex' else default_settings())
        codex_controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        api_form = ttk.Frame(form)
        api_form.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        model = self._field(api_form, 'Exact model ID', 0)
        base = self._field(api_form, 'API base URL', 1)
        env = self._field(api_form, 'API key environment-variable name', 2)
        provider.set(PROVIDER_LABELS[current['provider']])
        model.set(current['model'])
        base.set(current['base_url'])
        env.set(current['api_key_env'])
        last_provider = [current['provider']]
        def selected_key():
            return keys[list(PROVIDER_LABELS.values()).index(provider.get())]
        def api_values():
            return {'provider': selected_key(), 'model': model.get(), 'base_url': base.get(), 'api_key_env': env.get()}
        ollama_controls = OllamaThinkingControls(form, api_values, current if current['provider'] == 'ollama' else {})
        ollama_controls.grid(row=3, column=0, columnspan=2, sticky='ew')
        ollama_context = OllamaContextControls(form, current if current['provider'] == 'ollama' else {})
        ollama_context.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        for variable in (model, base, env):
            variable.trace_add('write', ollama_controls.inputs_changed)
        def changed(*_):
            key = selected_key()
            if key != last_provider[0]:
                defaults = default_settings(key)
                model.set('')
                base.set(defaults['base_url'])
                env.set(defaults['api_key_env'])
                last_provider[0] = key
            ollama_controls.grid() if key == 'ollama' else ollama_controls.grid_remove()
            ollama_context.grid() if key == 'ollama' else ollama_context.grid_remove()
            if key == 'codex':
                codex_controls.grid()
                api_form.grid_remove()
            else:
                codex_controls.grid_remove()
                api_form.grid()
        provider.trace_add('write', changed)
        changed()
        ttk.Label(frame, text='These settings apply to Job Radar generation and improvement. They do not change your open chat. Ollama thinking choices depend on the selected model; use Load thinking options to check them.',
                  wraplength=720, style='Muted.TLabel').pack(anchor='w', pady=(14, 7))
        ttk.Label(frame, text='Enter a variable name such as OPENAI_API_KEY, never the secret key itself. Set that variable in your operating system and restart Job Radar. Local Ollama can use an empty key-variable field.',
                  wraplength=720, style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        ttk.Label(frame, text='Ollama (local) uses http://localhost:11434/api for its native thinking controls. The existing compatible connection uses /v1. Choose a downloaded model for local processing; Ollama can also route cloud models remotely.',
                  wraplength=720, style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        feedback = tk.StringVar(value='Settings and model names stay local; secret keys are not stored here.')
        ttk.Label(frame, textvariable=feedback, wraplength=720).pack(anchor='w')
        def values():
            selected = api_values()
            if selected_key() == 'codex':
                selected.update(codex_controls.values())
            elif selected_key() == 'ollama':
                selected['ollama_thinking'] = ollama_controls.value()
                selected['ollama_context_tokens'] = ollama_context.value()
            return selected
        def check():
            try:
                feedback.set(settings_check(values())['detail'])
            except (ValueError, OSError) as exc:
                feedback.set(str(exc))
        def save():
            try:
                self.service.save_ai_settings(values())
                self._update_ai_labels()
                self.status.set('AI settings saved. No document data was sent.')
                window.destroy()
            except (ValueError, OSError) as exc:
                feedback.set(str(exc))
        buttons = ttk.Frame(window, padding=(20, 10))
        buttons.pack(side='bottom', fill='x')
        self._button(buttons, 'Save settings', save, primary=True)
        self._button(buttons, 'Check settings locally', check)
        self._button(buttons, 'Cancel', window.destroy, side='right')

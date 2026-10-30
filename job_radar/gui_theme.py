"""Job Radar's own desktop palette. Uses only the standard Tk/ttk library."""
from tkinter import ttk

COLORS = {'canvas': '#edf2f5', 'paper': '#ffffff', 'ink': '#21364b',
          'muted': '#576d80', 'navy': '#173452', 'accent': '#176e78',
          'line': '#d7e1e8', 'soft': '#f5f8fa', 'selection': '#dceef0'}


def apply_theme(window):
    c = COLORS
    style = ttk.Style(window)
    if 'clam' in style.theme_names():
        style.theme_use('clam')
    window.configure(background=c['canvas'])
    window.option_add('*Font', '{Segoe UI} 10')
    for name, value in {'background': c['paper'], 'foreground': c['ink'],
                        'selectBackground': c['selection'], 'selectForeground': c['navy'],
                        'insertBackground': c['ink'], 'relief': 'flat',
                        'highlightThickness': 1, 'highlightBackground': c['line'],
                        'highlightColor': c['accent']}.items():
        window.option_add('*Text.' + name, value)
    style.configure('.', font=('Segoe UI', 10), background=c['paper'], foreground=c['ink'])
    style.configure('Shell.TFrame', background=c['canvas'])
    style.configure('Brand.TFrame', background=c['navy'])
    style.configure('Brand.TLabel', font=('Segoe UI', 23, 'bold'), background=c['navy'], foreground='white')
    style.configure('BrandSub.TLabel', background=c['navy'], foreground='#d8e5ee')
    style.configure('Brand.TButton', background='#284a68', foreground='white', padding=(13, 8), borderwidth=0)
    style.map('Brand.TButton', background=[('active', '#365d7e')], foreground=[('disabled', '#8ea3b5')])
    style.configure('Heading.TLabel', font=('Segoe UI', 12, 'bold'), foreground=c['navy'])
    style.configure('Muted.TLabel', foreground=c['muted'])
    style.configure('Subtitle.TLabel', foreground=c['muted'], background=c['canvas'])
    style.configure('Metric.TLabel', font=('Segoe UI', 18, 'bold'), foreground=c['navy'])
    style.configure('Card.TFrame', background=c['paper'], relief='flat')
    style.configure('TButton', padding=(10, 7), background='#e9f0f5', borderwidth=0)
    style.map('TButton', background=[('active', '#dbe6ee')], foreground=[('disabled', '#8a99a6')])
    style.configure('Primary.TButton', background=c['accent'], foreground='white')
    style.map('Primary.TButton', background=[('disabled', '#dce7ef'), ('active', '#115862')],
              foreground=[('disabled', '#657888'), ('active', 'white')])
    style.configure('TEntry', padding=6, fieldbackground=c['paper'])
    style.configure('TCombobox', padding=6, fieldbackground=c['paper'])
    style.map('TCombobox', fieldbackground=[('readonly', c['paper'])], foreground=[('readonly', c['ink'])])
    style.configure('TNotebook', background=c['canvas'], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', padding=(12, 9), background='#e0e9ef')
    style.map('TNotebook.Tab', background=[('selected', c['paper'])], foreground=[('selected', c['accent'])])
    style.configure('Treeview', rowheight=34, background=c['paper'], fieldbackground=c['paper'], borderwidth=0)
    style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), padding=(8, 9), background=c['soft'])
    style.map('Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['navy'])])
    style.configure('TProgressbar', background=c['accent'], troughcolor='#e1e9f0', borderwidth=0)
    style.configure('TSeparator', background=c['line'])
    return style

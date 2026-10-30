"""Desktop launcher; keep failures visible when opened through pythonw."""
if __name__ == "__main__":
    try:
        from job_radar.gui import main
        main()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("Job Radar could not open", f"{exc}\n\nSee README.md in the Job Radar folder.", parent=window)
        window.destroy()

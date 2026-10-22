"""Shared light-workbench styling for the Tkinter user interface.

The application deliberately uses a restrained native-desktop palette instead
of a dark "NOC wall".  It keeps dense operational information readable during
long daily use on a Windows workstation.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


PALETTE = {
    "workspace": "#F4F7FB",
    "surface": "#FFFFFF",
    "surface_muted": "#EEF3F8",
    "sidebar": "#10233F",
    "sidebar_active": "#1B3A61",
    "sidebar_text": "#DCE8F7",
    "sidebar_muted": "#92A8C2",
    "ink": "#172033",
    "muted": "#68778C",
    "border": "#DDE5EF",
    "primary": "#2767D8",
    "primary_active": "#1E54B3",
    "success": "#16854B",
    "danger": "#D14343",
    "warning": "#BD7500",
    "info": "#3B6FA8",
}


def apply_workbench_theme(root: tk.Misc) -> ttk.Style:
    """Apply the consistent B+C light workbench theme and return its style."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        # ``clam`` is bundled with normal Tk installations.  Keep the current
        # theme if a heavily customised Python distribution omits it.
        pass

    root.configure(background=PALETTE["workspace"])
    # Tk parses option-database fonts as a Tcl list, so a multi-word Windows
    # font family needs braces rather than a plain space-separated string.
    root.option_add("*Font", "{Microsoft YaHei UI} 10")

    style.configure(".", font=("Microsoft YaHei UI", 10), foreground=PALETTE["ink"])
    style.configure("Workbench.TFrame", background=PALETTE["workspace"])
    style.configure("Card.TFrame", background=PALETTE["surface"])
    style.configure("Topbar.TFrame", background=PALETTE["surface"])
    style.configure("Footer.TFrame", background=PALETTE["surface"])
    style.configure("Sidebar.TFrame", background=PALETTE["sidebar"])
    style.configure("Card.TLabel", background=PALETTE["surface"], foreground=PALETTE["ink"])
    style.configure("Muted.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])
    style.configure("PageTitle.TLabel", background=PALETTE["workspace"], foreground=PALETTE["ink"], font=("Microsoft YaHei UI", 18, "bold"))
    style.configure("SectionTitle.TLabel", background=PALETTE["surface"], foreground=PALETTE["ink"], font=("Microsoft YaHei UI", 11, "bold"))
    style.configure("MetricValue.TLabel", background=PALETTE["surface"], foreground=PALETTE["ink"], font=("Microsoft YaHei UI", 22, "bold"))
    style.configure("MetricHint.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=("Microsoft YaHei UI", 9))
    style.configure("TopbarTitle.TLabel", background=PALETTE["surface"], foreground=PALETTE["ink"], font=("Microsoft YaHei UI", 13, "bold"))
    style.configure("TopbarSubtle.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=("Microsoft YaHei UI", 9))
    style.configure("FooterTitle.TLabel", background=PALETTE["surface"], foreground=PALETTE["ink"], font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("FooterText.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])

    style.configure(
        "Primary.TButton",
        background=PALETTE["primary"], foreground="#FFFFFF", borderwidth=0,
        padding=(13, 7), font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("active", PALETTE["primary_active"]), ("disabled", "#AFC5E9")],
        foreground=[("disabled", "#F4F7FB")],
    )
    style.configure(
        "Secondary.TButton",
        background=PALETTE["surface"], foreground=PALETTE["ink"], borderwidth=1,
        relief="solid", padding=(12, 7),
    )
    style.map(
        "Secondary.TButton",
        background=[("active", PALETTE["surface_muted"]), ("disabled", PALETTE["surface_muted"])],
        foreground=[("disabled", PALETTE["muted"])],
    )
    style.configure(
        "Quiet.TButton",
        background=PALETTE["surface"], foreground=PALETTE["primary"], borderwidth=0,
        padding=(8, 5),
    )
    style.map("Quiet.TButton", background=[("active", PALETTE["surface_muted"])])

    style.configure("StatusGood.TLabel", background="#E7F7ED", foreground=PALETTE["success"], padding=(8, 3), font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("StatusWarn.TLabel", background="#FFF3D8", foreground=PALETTE["warning"], padding=(8, 3), font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("StatusBad.TLabel", background="#FCE9E9", foreground=PALETTE["danger"], padding=(8, 3), font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("Filter.TLabel", background="#EAF1FF", foreground=PALETTE["primary"], padding=(8, 3), font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("DangerPill.TLabel", background="#FCE9E9", foreground=PALETTE["danger"], padding=(7, 3), font=("Microsoft YaHei UI", 9, "bold"))

    style.configure(
        "Treeview",
        background=PALETTE["surface"], fieldbackground=PALETTE["surface"], foreground=PALETTE["ink"],
        rowheight=33, borderwidth=0, relief="flat",
    )
    style.map("Treeview", background=[("selected", "#DCE9FF")], foreground=[("selected", PALETTE["ink"])])
    style.configure(
        "Treeview.Heading",
        background=PALETTE["surface_muted"], foreground=PALETTE["muted"], relief="flat",
        padding=(9, 8), font=("Microsoft YaHei UI", 9, "bold"),
    )
    style.map("Treeview.Heading", background=[("active", "#E5ECF4")])
    style.configure(
        "Inspection.Horizontal.TProgressbar",
        troughcolor="#E7EDF5", background=PALETTE["primary"], bordercolor="#E7EDF5",
        lightcolor=PALETTE["primary"], darkcolor=PALETTE["primary"], thickness=7,
    )
    style.configure("TSeparator", background=PALETTE["border"])
    return style

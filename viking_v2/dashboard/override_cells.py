"""Keep the Override mark yellow without recoloring a whole Treeview row."""
import tkinter as tk
from tkinter import ttk


class OverrideCells:
    def __init__(self, tree):
        self.tree, self.labels, self.job = tree, {}, None
        for event in ("<Configure>", "<<TreeviewSelect>>", "<<TreeviewOpen>>", "<<TreeviewClose>>"):
            tree.bind(event, self.schedule, add="+")
        tree.bind("<Destroy>", self.close, add="+")
        for option in ("xscrollcommand", "yscrollcommand"):
            previous = tree.tk.splitlist(tree.cget(option))
            def changed(*args, previous=previous):
                if previous:
                    tree.tk.call(*previous, *args)
                self.schedule()
            tree.configure(**{option: changed})

    def schedule(self, _event=None):
        if self.job is None and self.tree.winfo_exists():
            self.job = self.tree.after_idle(self.draw)

    def draw(self):
        self.job = None
        if not self.tree.winfo_exists():
            return
        active = set()
        selected = set(self.tree.selection())
        style = ttk.Style(self.tree)
        name = str(self.tree.cget("style")) or "Treeview"
        for iid in getattr(self.tree, "_viking_override_rows", {}):
            if not self.tree.exists(iid):
                continue
            bbox = self.tree.bbox(iid, "Override")
            if not bbox:
                continue
            x, y, width, height = bbox
            if x < 0 or x + width > self.tree.winfo_width() or y + height > self.tree.winfo_height():
                continue
            active.add(iid)
            if iid in selected:
                background = style.lookup(name, "background", ("selected",)) or "#5545A6"
            else:
                background = next((self.tree.tag_configure(tag, "background")
                                   for tag in self.tree.item(iid, "tags") if self.tree.tag_configure(tag, "background")),
                                  style.lookup(name, "background") or "#22262D")
            label = self.labels.get(iid)
            if label is None:
                label = self.labels[iid] = tk.Label(self.tree, text="X", fg="#F59E0B", borderwidth=0,
                                                   font=("Segoe UI", 16, "bold"), cursor="arrow")
                for event in ("<Motion>", "<Leave>", "<Button-1>", "<ButtonRelease-1>", "<Button-3>", "<MouseWheel>"):
                    label.bind(event, lambda e, event=event, label=label: self.forward(e, event, label))
            label.configure(bg=background)
            label.place(x=x + 1, y=y + 1, width=max(1, width - 2), height=max(1, height - 2))
            label.lift()
        for iid, label in list(self.labels.items()):
            if iid not in active:
                label.destroy()
                del self.labels[iid]

    def forward(self, event, sequence, label):
        options = {"x": label.winfo_x() + event.x, "y": label.winfo_y() + event.y, "state": event.state}
        if sequence == "<MouseWheel>":
            options["delta"] = event.delta
        self.tree.event_generate(sequence, **options)
        self.schedule()
        return "break"

    def close(self, event):
        if event.widget is self.tree and self.job is not None:
            self.tree.after_cancel(self.job)
            self.job = None

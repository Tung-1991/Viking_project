"""Display-only cell alignment and column rules for the running books."""
from tkinter import Frame, Label, ttk
from tkinter import font as tkfont

from .view import COL_RUNNING_SELECTED


def _fit_lines(text, metrics, width):
    """Shorten the painted text; tree values remain intact for hover details."""
    lines = []
    for line in text.split("\n"):
        if metrics.measure(line) <= width:
            lines.append(line)
            continue
        if metrics.measure("…") > width:
            lines.append("")
            continue
        low, high = 0, len(line)
        while low < high:
            middle = (low + high + 1) // 2
            if metrics.measure(line[:middle] + "…") <= width:
                low = middle
            else:
                high = middle - 1
        lines.append(line[:low].rstrip() + "…")
    return "\n".join(lines)


class RunningTableCells:
    def __init__(self, tree):
        self.tree, self.labels, self.rules, self.job = tree, {}, {}, None
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

    def bind_events(self, widget):
        for sequence in ("<Motion>", "<Leave>", "<Button-1>", "<ButtonRelease-1>",
                         "<B1-Motion>", "<Button-3>", "<MouseWheel>"):
            widget.bind(sequence, lambda event, sequence=sequence, widget=widget:
                        self.forward(event, sequence, widget))

    def forward(self, event, sequence, widget):
        options = {"x": widget.winfo_x() + event.x, "y": widget.winfo_y() + event.y,
                   "state": getattr(event, "state", 0)}
        if sequence == "<MouseWheel>":
            options["delta"] = event.delta
        self.tree.event_generate(sequence, **options)
        widget.configure(cursor=self.tree.cget("cursor"))
        self.schedule()
        return "break"

    def draw(self):
        self.job = None
        tree = self.tree
        if not tree.winfo_exists():
            return
        from .tables import _running_scale
        scale = _running_scale(tree.winfo_toplevel())
        style = ttk.Style(tree)
        name = str(tree.cget("style")) or "Treeview"
        font = style.lookup(name, "font")
        metrics = tkfont.Font(root=tree, font=font)
        padding = round(8 * scale)
        height = int(style.lookup(name, "rowheight") or 20)
        width, bottom = tree.winfo_width(), tree.winfo_height()
        shown = tuple(tree["displaycolumns"])
        columns = tuple(tree["columns"]) if shown == ("#all",) else shown
        selected = set(tree.selection())
        visible = dict.fromkeys(tree.identify_row(y) for y in range(0, bottom, max(1, height // 2)))
        active = set()
        sample = next((iid for iid in visible if iid and tree.bbox(iid)), "")
        for iid in visible:
            if not iid:
                continue
            if iid in selected:
                background = style.lookup(name, "background", ("selected",)) or COL_RUNNING_SELECTED
                foreground = style.lookup(name, "foreground", ("selected",)) or "#FFFFFF"
            else:
                tags = tree.item(iid, "tags")
                background = next((tree.tag_configure(tag, "background") for tag in tags
                                   if tree.tag_configure(tag, "background")), style.lookup(name, "background") or "#22262D")
                foreground = next((tree.tag_configure(tag, "foreground") for tag in tags
                                   if tree.tag_configure(tag, "foreground")), style.lookup(name, "foreground") or "#E8EBEF")
            for column in columns:
                if column == "Override" and iid in getattr(tree, "_viking_override_rows", {}):
                    continue  # The existing yellow marker owns this cell.
                box = tree.bbox(iid, column)
                if not box:
                    continue
                x, y, cell_width, cell_height = box
                if x >= width or x + cell_width <= 0 or y < 0 or y >= bottom:
                    continue
                key = (iid, column)
                active.add(key)
                label = self.labels.get(key)
                if label is None:
                    label = self.labels[key] = Label(tree, borderwidth=0, takefocus=False, justify="left",
                                                     anchor="center" if column == "STT" else "n" if column in {"Bot", "Override"} else "nw")
                    self.bind_events(label)
                layout = (tree.set(iid, column), cell_width, font, padding)
                if getattr(label, "_running_layout", None) != layout:
                    label._running_text = _fit_lines(layout[0], metrics, max(0, cell_width - 2 - 2 * padding))
                    label._running_layout = layout
                options = dict(text=label._running_text, bg=background, fg=foreground, font=font,
                               padx=padding, pady=round(4 * scale))
                if getattr(label, "_running_options", None) != options:
                    label.configure(**options)
                    label._running_options = options
                label.place(x=x + 1, y=y, width=max(1, cell_width - 2), height=min(cell_height, bottom - y))
                label.lift()
        for key in self.labels.keys() - active:
            self.labels.pop(key).destroy()
        active_rules = set()
        total = sum(int(tree.column(column, "width")) for column in columns)
        tree_width = int(tree.column("#0", "width")) if "tree" in tree.tk.splitlist(tree.cget("show")) else 0
        left = tree_width - round(tree.xview()[0] * (total + tree_width)) + 1
        for column in columns:
            box = tree.bbox(sample, column) if sample else ()
            right = box[0] + box[2] if box else left + int(tree.column(column, "width"))
            left = right
            if not 0 < right < width:
                continue
            active_rules.add(column)
            rule = self.rules.get(column)
            if rule is None:
                rule = self.rules[column] = Frame(tree, bg="#424852", borderwidth=0, takefocus=False)
                self.bind_events(rule)
            rule.place(x=right - 1, y=0, width=2, height=bottom)
            rule.lift()
        for key in self.rules.keys() - active_rules:
            self.rules.pop(key).destroy()

    def close(self, event):
        if event.widget is self.tree and self.job is not None:
            self.tree.after_cancel(self.job)
            self.job = None

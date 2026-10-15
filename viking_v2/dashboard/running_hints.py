"""One delayed tooltip per running table, following its column and row."""
from .hint_box import contains_pointer, fixed_hint

COLUMN_HINTS = {
    "STT": "Số thứ tự trong sổ đang xem. 1.1 là lệnh con thuộc vị thế số 1.",
    "Ticket": "Mã vị thế/lệnh. Chuột phải để quản lý; hủy lệnh không xóa cổ đã khớp.",
    "Time": "Thời điểm mở vị thế hoặc tạo yêu cầu lệnh.",
    "Order": "Mã, giá vốn, số cổ và thời gian; TAY = đặt trong app, DNSE = giao dịch ngoài app.\n"
             "ARM/START là giá bật theo dõi bảo vệ. Chi tiết mua/bán khớp xem trong hint của dòng.",
    "Targets": "SL/TP hiện giá kích hoạt và lãi/lỗ dự kiến trên số cổ còn giữ.\n"
               "ARM/START bật theo dõi; PROTECT là mốc giá lùi để xét bán. Chưa đồng nghĩa đã khớp.",
    "CostInfo": "Phí đã ghi nhận hoặc ước tính; CHỜ DNSE nghĩa là chưa có phí xác nhận.",
    "RR": "R là mức lỗ SL, E là mức lãi TP; tỷ lệ E/R theo cấu hình đang áp dụng.",
    "PnL_MAE_MFE": "PnL gồm lãi/lỗ đã bán và phần còn giữ; % trên vốn mua gốc.\n"
                   "MAE/MFE hiện % biến động, giá thấp/cao và số tiền trên phần cổ còn giữ, trước phí.",
    "Status": "Trạng thái cổ về và quản lý tự bán. Di chuột vào dòng để xem chi tiết.\n"
              "Tạm dừng tự bán chặn SL/TP/PROTECT/E mới; lệnh đã gửi broker cần hủy riêng.",
    "Bot": "Bật: quản lý theo RULE. Alert: chỉ tín hiệu. Bán Alert: tạm dừng tự bán.\nBOT chung chỉ bật/tắt mua tự động mới.",
    "Override": "X vàng: có cấu hình riêng. Di chuột để xem những mục ghi đè; chuột phải để chỉnh.",
}


class RunningTableHint:
    def __init__(self, tree, columns):
        self.tree, self.columns = tree, columns
        self.target, self.timer, self.popup = None, None, None
        self.leave_timer = None
        tree.bind("<Motion>", self.motion, add="+")
        tree.bind("<Leave>", self.leave, add="+")
        tree.bind("<ButtonPress>", self.hide, add="+")
        tree.bind("<Destroy>", self.hide, add="+")

    def text_for(self, row, column):
        text = COLUMN_HINTS.get(column, "")
        if row:
            hints = getattr(self.tree, "_viking_row_hints", {}).get(row, {})
            text = hints.get(column, text)
            if column not in hints:
                values = self.tree.item(row, "values")
                index = self.columns.index(column)
                if index < len(values) and values[index]:
                    text += "\n" + str(values[index])
        return text

    def motion(self, event):
        if self.leave_timer is not None:
            self.tree.after_cancel(self.leave_timer)
            self.leave_timer = None
        region = self.tree.identify_region(event.x, event.y)
        identifier = self.tree.identify_column(event.x)
        if region not in {"heading", "cell"} or not identifier.startswith("#"):
            self.hide()
            return
        index = int(identifier[1:]) - 1
        if index < 0 or index >= len(self.columns):
            self.hide()
            return
        row = self.tree.identify_row(event.y) if region == "cell" else ""
        # #n identifies a displayed column, which may differ from columns[n].
        column = self.tree.column(identifier, "id")
        target = (row, column)
        if target != self.target:
            self.hide()
            self.target = target
            self.timer = self.tree.after(450, lambda: self.show(target, event.x, event.y))

    def show(self, target, x, y):
        self.timer = None
        if self.target != target or not self.tree.winfo_exists():
            return
        text = self.text_for(*target)
        if not text:
            return
        host = self.tree.winfo_toplevel()
        row, column = target
        self.popup = fixed_hint(host, text, compact=not row or column != "Status")
        for widget in (self.popup, *self.popup.winfo_children()):
            widget.bind("<Leave>", self.leave, add="+")
        self.popup.update_idletasks()
        width, height = self.popup.winfo_reqwidth(), self.popup.winfo_reqheight()
        px = self.tree.winfo_rootx() - host.winfo_rootx() + x + 12
        py = self.tree.winfo_rooty() - host.winfo_rooty() + y + 24
        if py + height > host.winfo_height() - 8:
            py -= height + 40
        self.popup.place(x=max(8, min(px, host.winfo_width() - width - 8)),
                         y=max(8, min(py, host.winfo_height() - height - 8)))
        self.popup.lift()

    def leave(self, _event=None):
        if self.leave_timer is not None:
            self.tree.after_cancel(self.leave_timer)
        def check():
            self.leave_timer = None
            if not contains_pointer(self.popup) and not contains_pointer(self.tree):
                self.hide()
        self.leave_timer = self.tree.after(120, check)

    def hide(self, _event=None):
        if self.leave_timer is not None:
            self.tree.after_cancel(self.leave_timer)
            self.leave_timer = None
        if self.timer is not None:
            self.tree.after_cancel(self.timer)
            self.timer = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
        self.target = None

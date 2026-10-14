"""One delayed tooltip per running table, following its column and row."""
import tkinter as tk

COLUMN_HINTS = {
    "Ticket": "Mã vị thế/lệnh. Chuột phải để quản lý; hủy lệnh không xóa cổ đã khớp.",
    "Time": "Thời điểm mở vị thế hoặc tạo yêu cầu lệnh.",
    "Order": "Giá vốn và số cổ đang giữ; lịch sử mua/bán khớp xem trong hint của dòng.",
    "Targets": "SL/TP tính từ giá vốn. Chạm ngưỡng là điều kiện bán, không bảo đảm giá khớp.",
    "CostInfo": "Phí đã ghi nhận hoặc ước tính; CHỜ DNSE nghĩa là chưa có phí xác nhận.",
    "RR": "R là mức lỗ SL, E là mức lãi TP; tỷ lệ E/R theo cấu hình đang áp dụng.",
    "PnL_MAE_MFE": "PnL gồm lãi/lỗ đã bán và phần còn giữ; % trên vốn mua gốc.\n"
                   "MAE/MFE hiện giá thấp/cao và % so với giá vốn trong khoảng theo dõi.",
    "Status": "Trạng thái cổ về và quản lý tự bán. Di chuột vào dòng để xem chi tiết.\n"
              "Tạm dừng tự bán chặn SL/TP/PROTECT/E mới; lệnh đã gửi broker cần hủy riêng.",
    "X": "Hủy yêu cầu còn chờ. Không hoàn tác phần đã khớp; không xóa vị thế.",
}


class RunningTableHint:
    def __init__(self, tree, columns):
        self.tree, self.columns = tree, columns
        self.target, self.timer, self.popup = None, None, None
        tree.bind("<Motion>", self.motion, add="+")
        tree.bind("<Leave>", self.hide, add="+")
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
        target = (row, self.columns[index])
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
        self.popup = tk.Label(host, text=text, justify="left", wraplength=max(240, min(650, host.winfo_width() - 40)),
                              font=("Segoe UI", 13), bg="#252A31", fg="#F5F7FA", padx=12, pady=10,
                              relief="solid", borderwidth=1)
        self.popup.update_idletasks()
        width, height = self.popup.winfo_reqwidth(), self.popup.winfo_reqheight()
        px = self.tree.winfo_rootx() - host.winfo_rootx() + x + 12
        py = self.tree.winfo_rooty() - host.winfo_rooty() + y + 24
        if py + height > host.winfo_height() - 8:
            py -= height + 40
        self.popup.place(x=max(8, min(px, host.winfo_width() - width - 8)),
                         y=max(8, min(py, host.winfo_height() - height - 8)))
        self.popup.lift()

    def hide(self, _event=None):
        if self.timer is not None:
            self.tree.after_cancel(self.timer)
            self.timer = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
        self.target = None

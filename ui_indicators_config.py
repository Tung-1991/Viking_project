# -*- coding: utf-8 -*-
# FILE: ui_indicators_config.py
# Dynamic indicator config popup with global + per-group overrides.

import copy
import customtkinter as ctk
from tkinter import messagebox


TRIGGER_MODES = ["STRICT_CLOSE", "REALTIME_TICK"]
GROUPS = ["G0", "G1", "G2", "G3"]


def _cast_value(value, original_type):
    if original_type == bool:
        return str(value).lower() in ["true", "1", "t", "y", "yes"]
    if original_type == float:
        return float(value)
    if original_type == int:
        return int(value)
    return value


def open_indicator_config_popup(
    parent,
    ind_name,
    current_params,
    save_callback,
    group_params=None,
    global_trigger_mode="STRICT_CLOSE",
    group_trigger_modes=None,
    group_labels=None,
):
    """
    Backward-compatible popup.
    - Global params stay in indicators[*].params.
    - Group overrides are saved in indicators[*].group_params.
    - Group trigger overrides are saved in indicators[*].group_trigger_modes.
    """
    group_params = copy.deepcopy(group_params or {})
    group_trigger_modes = copy.deepcopy(group_trigger_modes or {})
    group_labels = group_labels or {g: g for g in GROUPS}

    top = ctk.CTkToplevel(parent)
    top.title(f"Config params: {ind_name.upper()}")
    top.geometry("620x640")
    top.attributes("-topmost", True)
    top.focus_force()
    top.grab_set()

    ctk.CTkLabel(
        top,
        text=f"TECHNICAL PARAMS: {ind_name.upper()}",
        font=("Roboto", 15, "bold"),
        text_color="#E040FB",
    ).pack(pady=(18, 8))

    ctk.CTkLabel(
        top,
        text="GLOBAL la mac dinh. G0-G3 chi luu override khi khac GLOBAL.",
        font=("Arial", 12, "italic"),
        text_color="#BDBDBD",
    ).pack(pady=(0, 8))

    tabview = ctk.CTkTabview(top)
    tabview.pack(fill="both", expand=True, padx=20, pady=10)

    entries_by_scope = {}
    trigger_by_group = {}
    base_params = copy.deepcopy(current_params or {})

    def build_param_rows(frame, scope, values):
        scroll_frame = ctk.CTkScrollableFrame(frame, fg_color="#2b2b2b", corner_radius=8)
        scroll_frame.pack(fill="both", expand=True, padx=8, pady=8)
        scroll_frame.columnconfigure(0, weight=1)
        scroll_frame.columnconfigure(1, weight=1)

        entries = {}
        if not base_params:
            ctk.CTkLabel(
                scroll_frame,
                text="Indicator nay khong co params tuy chinh.",
                font=("Arial", 13, "italic"),
                text_color="gray",
            ).grid(row=0, column=0, columnspan=2, padx=15, pady=20)
            entries_by_scope[scope] = entries
            return

        row = 0
        for key, global_value in base_params.items():
            ctk.CTkLabel(
                scroll_frame,
                text=key.upper(),
                font=("Roboto", 12, "bold"),
            ).grid(row=row, column=0, padx=15, pady=10, sticky="w")

            ent = ctk.CTkEntry(scroll_frame, width=130, justify="center", font=("Consolas", 13))
            ent.insert(0, str(values.get(key, global_value)))
            ent.grid(row=row, column=1, padx=15, pady=10, sticky="e")

            entries[key] = {"widget": ent, "original_type": type(global_value)}
            ctk.CTkFrame(scroll_frame, height=1, fg_color="#444").grid(
                row=row + 1, column=0, columnspan=2, sticky="ew", padx=10
            )
            row += 2

        entries_by_scope[scope] = entries

    global_tab = tabview.add("GLOBAL")
    build_param_rows(global_tab, "GLOBAL", base_params)

    for group in GROUPS:
        tab = tabview.add(group_labels.get(group, group))
        trigger_frame = ctk.CTkFrame(tab, fg_color="transparent")
        trigger_frame.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(trigger_frame, text="Trigger:", font=("Roboto", 12, "bold")).pack(
            side="left", padx=(4, 8)
        )
        trigger_var = ctk.StringVar(
            value=group_trigger_modes.get(group, f"GLOBAL ({global_trigger_mode})")
        )
        trigger_by_group[group] = trigger_var
        ctk.CTkComboBox(
            trigger_frame,
            values=[f"GLOBAL ({global_trigger_mode})"] + TRIGGER_MODES,
            variable=trigger_var,
            width=180,
        ).pack(side="left")

        def reset_group(g=group):
            for key, data in entries_by_scope.get(g, {}).items():
                data["widget"].delete(0, "end")
                data["widget"].insert(0, str(base_params.get(key, "")))
            trigger_by_group[g].set(f"GLOBAL ({global_trigger_mode})")

        ctk.CTkButton(
            trigger_frame,
            text="Reset group",
            width=110,
            fg_color="#5D4037",
            hover_color="#4E342E",
            command=reset_group,
        ).pack(side="right", padx=4)

        resolved_params = copy.deepcopy(base_params)
        if isinstance(group_params.get(group), dict):
            resolved_params.update(group_params[group])
        build_param_rows(tab, group, resolved_params)

    def read_params(scope):
        result = {}
        for key, data in entries_by_scope.get(scope, {}).items():
            result[key] = _cast_value(data["widget"].get(), data["original_type"])
        return result

    def on_save():
        try:
            new_global_params = read_params("GLOBAL")
            new_group_params = {}
            new_group_trigger_modes = {}

            for group in GROUPS:
                scoped_params = read_params(group)
                overrides = {
                    key: value
                    for key, value in scoped_params.items()
                    if new_global_params.get(key) != value
                }
                if overrides:
                    new_group_params[group] = overrides

                trigger_value = trigger_by_group[group].get()
                if trigger_value in TRIGGER_MODES and trigger_value != global_trigger_mode:
                    new_group_trigger_modes[group] = trigger_value

            save_callback(new_global_params, new_group_params, new_group_trigger_modes)
            top.destroy()
        except ValueError:
            messagebox.showerror(
                "Invalid data",
                "Please enter valid Int/Float values for numeric fields.",
                parent=top,
            )

    btn_frame = ctk.CTkFrame(top, fg_color="transparent")
    btn_frame.pack(fill="x", padx=20, pady=20)

    ctk.CTkButton(
        btn_frame,
        text="HUY",
        fg_color="#D50000",
        hover_color="#B71C1C",
        width=120,
        height=40,
        font=("Roboto", 13, "bold"),
        command=top.destroy,
    ).pack(side="left", padx=10)
    ctk.CTkButton(
        btn_frame,
        text="LUU THONG SO",
        fg_color="#00C853",
        hover_color="#009624",
        height=40,
        font=("Roboto", 13, "bold"),
        command=on_save,
    ).pack(side="right", padx=10, fill="x", expand=True)

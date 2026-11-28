"""Manual Windows GUI for Notion authentication and DPAPI storage.

This executable is inert until manually launched. No engine activation.
"""
from __future__ import annotations

import hashlib
import queue
import threading
import tkinter as tk
from tkinter import ttk

from .notion_n02_secret_store import CredentialStoreError, load_token, save_token
from .notion_n02_transport import (
    REPOSITORY,
    TEST_PAGE_ID,
    NotionReadOnlyClient,
    NotionConnectionError,
    diagnostic,
    validate_token,
)


def _fingerprint(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class ConnectionManager:
    def __init__(self, window: tk.Tk):
        self.window = window
        window.title("Iron God — Notion Connection Manager v2 (manual, read-only)")
        window.geometry("640x425")
        window.minsize(560, 385)
        self.token_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Not tested. N02 does not activate the engine.")
        self._verified_fingerprint: bytes | None = None
        self._messages: queue.Queue[tuple[str, str, bytes | None, bool]] = queue.Queue()
        self._working = False

        outer = ttk.Frame(window, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Iron God Knowledge Bridge", font=("Segoe UI", 19, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        ttk.Label(
            outer, text="Manual, read-only Notion connectivity check • No engine activation",
            wraplength=570,
        ).pack(anchor="w", pady=(0, 16))
        ttk.Label(outer, text="Test page: Iron God multi-agent architecture").pack(anchor="w")
        ttk.Label(outer, text="Repository: " + REPOSITORY).pack(anchor="w", pady=(0, 12))
        ttk.Label(outer, text="Paste your Notion integration token here (never in chat):").pack(
            anchor="w"
        )
        self.token_entry = ttk.Entry(outer, textvariable=self.token_var, show="•")
        self.token_entry.pack(fill="x", pady=(4, 10))
        self.token_entry.focus_set()

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(4, 10))
        self.test_button = ttk.Button(controls, text="Test connection", command=self.test_current_token)
        self.test_button.pack(side="left", padx=(0, 8))
        self.save_button = ttk.Button(
            controls, text="Save securely", command=self.save_current_token, state="disabled",
        )
        self.save_button.pack(side="left", padx=(0, 8))
        self.test_saved_button = ttk.Button(
            controls, text="Test saved credential", command=self.test_saved_token
        )
        self.test_saved_button.pack(side="left")

        ttk.Label(outer, text="Verification result", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(12, 3)
        )
        ttk.Label(outer, textvariable=self.status_var, wraplength=565, justify="left").pack(
            anchor="w", fill="x", pady=(0, 12)
        )
        ttk.Label(
            outer,
            text=(
                "Your token is encrypted with Windows DPAPI for this Windows user only. "
                "Saving is permitted before a successful page-access test. "
                "The app uses HTTPS GET to verify the approved page. "
                "It never writes to Notion or starts Iron God."
            ),
            wraplength=570,
        ).pack(anchor="w", pady=(5, 0))
        self.token_var.trace_add("write", self._on_token_changed)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(100, self._poll_results)

    def _set_working(self, active: bool) -> None:
        self._working = active
        self.test_button.config(state="disabled" if active else "normal")
        self.test_saved_button.config(state="disabled" if active else "normal")
        try:
            validate_token(self.token_var.get())
            token_valid = True
        except NotionConnectionError:
            token_valid = False
        self.save_button.config(state="normal" if not active and token_valid else "disabled")

    def _on_token_changed(self, *_: object) -> None:
        self._verified_fingerprint = None
        self._set_working(self._working)
        if not self._working and self.token_var.get():
            self.status_var.set(
                "Token entered. Save securely stores it locally without requiring "
                "Notion page access. Test connection separately to check permissions."
            )

    def _start_test(self, token: str, *, saved: bool = False) -> None:
        if self._working:
            return
        self._verified_fingerprint = None
        self._set_working(True)
        self.status_var.set("Testing authentication and exact page access…")
        threading.Thread(target=self._worker, args=(token, saved), daemon=True).start()

    def _worker(self, token: str, saved: bool) -> None:
        try:
            client = NotionReadOnlyClient(token)
            client.check_authentication()
        except Exception as exc:
            self._messages.put(("error", "Authentication failed: " + diagnostic(exc), None, saved))
            return
        try:
            record = client.read_approved_page(
                TEST_PAGE_ID, repository=REPOSITORY, allowed_page_ids=[TEST_PAGE_ID],
            )
            message = (
                "PASS: Authentication and page retrieval succeeded. "
                f"Advisory document: {record['title']}; "
                f"blocks: {len(record['block_ids'])}. "
                "The Iron God engine remains disconnected."
            )
            self._messages.put(("success", message, _fingerprint(token), saved))
        except Exception as exc:
            self._messages.put((
                "page_error", "Authentication PASS. Page access / content validation failed: "
                + diagnostic(exc), None, saved,
            ))

    def _poll_results(self) -> None:
        try:
            while True:
                state, message, fingerprint, saved = self._messages.get_nowait()
                self.status_var.set(message)
                self._verified_fingerprint = fingerprint if state == "success" and not saved else None
                self._set_working(False)
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            self.window.after(100, self._poll_results)

    def test_current_token(self) -> None:
        self._start_test(self.token_var.get())

    def test_saved_token(self) -> None:
        try:
            token = load_token()
        except (CredentialStoreError, ValueError):
            self.status_var.set(
                "NO SAVED CREDENTIAL: Enter your Notion token and click Save securely "
                "before using Test saved credential."
            )
            return
        self._start_test(token, saved=True)

    def save_current_token(self) -> None:
        """User may save a well-formed token even if page permission is not granted."""
        if self._working:
            return
        token = self.token_var.get()
        try:
            validate_token(token)
            destination = save_token(token)
            self.token_var.set("")
            self._verified_fingerprint = None
            self._set_working(False)
            self.status_var.set(
                "SAVED: Encrypted credential stored in your Windows user profile: "
                + str(destination) + ". Click Test saved credential to check "
                "authentication and page access. Production integration is OFF."
            )
        except (CredentialStoreError, ValueError):
            self.status_var.set("SAVE FAILED: Unable to encrypt/store the credential.")

    def close(self) -> None:
        self.token_var.set("")
        self._verified_fingerprint = None
        self.window.destroy()


def main() -> int:
    window = tk.Tk()
    ConnectionManager(window)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

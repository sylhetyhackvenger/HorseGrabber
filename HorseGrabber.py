#!/usr/bin/env python3
"""
HORSEGRABBER
Author: SYLHETYHACKVENGER (THE-ERROR808)

Defensive Windows endpoint investigation and manual file quarantine.
Requires Windows 10/11 and Python 3.10+. PowerShell is used for read-only
Windows inventory. No network probing, credential collection, keylogging,
remote access, or attacker retaliation is implemented.
"""

from __future__ import annotations
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "HORSEGRABBER"
AUTHOR = "SYLHETYHACKVENGER (THE-ERROR808)"
BASE_DIR = Path.home() / "HorseGrabber"
REPORT_DIR = BASE_DIR / "Reports"
QUARANTINE_DIR = BASE_DIR / "Quarantine"
for directory in (REPORT_DIR, QUARANTINE_DIR):
    directory.mkdir(parents=True, exist_ok=True)

PS = shutil.which("powershell") or shutil.which("pwsh")
IS_WINDOWS = os.name == "nt"

def run_powershell(code: str, timeout: int = 45):
    """Run a read-only PowerShell inventory query and parse JSON output."""
    if not IS_WINDOWS or not PS:
        raise RuntimeError("HORSEGRABBER requires Windows PowerShell.")
    wrapped = "$ErrorActionPreference='SilentlyContinue'; " + code
    proc = subprocess.run(
        [PS, "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "RemoteSigned", "-Command", wrapped],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or "PowerShell query failed")
    output = proc.stdout.strip()
    if not output:
        return []
    try:
        data = json.loads(output)
        if data is None:
            return []
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return [{"RawOutput": output, "Warning": "Output was not JSON"}]

def ps_json(expression: str, timeout: int = 45):
    return run_powershell(f"{expression} | ConvertTo-Json -Depth 5 -Compress", timeout)

def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def is_protected_path(path: Path):
    """Refuse quarantine of core Windows and common application directories."""
    try:
        resolved = path.resolve()
    except Exception:
        return True
    protected_roots = [
        Path(os.environ.get("WINDIR", r"C:\Windows")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for root in protected_roots:
        try:
            if resolved == root.resolve() or root.resolve() in resolved.parents:
                return True
        except Exception:
            pass
    return False

class HorseGrabber:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} | Windows Threat Hunter")
        self.root.geometry("1180x720")
        self.root.minsize(900, 560)
        self.results = []
        self.busy = False
        self._build_ui()
        self.log("Ready. Run an inventory scan to begin.")
        if not IS_WINDOWS:
            self.log("WARNING: This application is designed for Windows.")

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(header, text=f"Author: {AUTHOR}  |  Defensive endpoint investigation",
                  font=("Segoe UI", 10)).pack(anchor="w")
        ttk.Label(header, text="No attacker attribution is inferred from IP addresses. Findings are leads, not proof.",
                  foreground="#9b5b00").pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        controls.pack(fill="x")
        buttons = [
            ("Run Full Hunt", self.full_hunt),
            ("Processes + Network", self.process_network),
            ("Persistence Hunt", self.persistence_hunt),
            ("Defender Check", self.defender_check),
            ("File / Folder Scan", self.file_scan),
            ("Hash Selected File", self.hash_file),
            ("Quarantine Selected File", self.quarantine_selected),
            ("Export Report", self.export_report),
        ]
        for i, (label, command) in enumerate(buttons):
            ttk.Button(controls, text=label, command=command).grid(
                row=i // 4, column=i % 4, padx=3, pady=4, sticky="ew")
        for i in range(4):
            controls.columnconfigure(i, weight=1)

        pane = ttk.Frame(self.root, padding=(12, 4))
        pane.pack(fill="both", expand=True)
        columns = ("severity", "category", "name", "details", "path", "pid")
        self.tree = ttk.Treeview(pane, columns=columns, show="headings", selectmode="browse")
        widths = {"severity": 85, "category": 145, "name": 180,
                  "details": 330, "path": 330, "pid": 65}
        for col in columns:
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=widths[col], minwidth=55)
        yscroll = ttk.Scrollbar(pane, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(pane, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        pane.rowconfigure(0, weight=1)
        pane.columnconfigure(0, weight=1)
        self.tree.tag_configure("HIGH", foreground="#b00020")
        self.tree.tag_configure("MEDIUM", foreground="#9b5b00")
        self.tree.tag_configure("INFO", foreground="#245b8f")

        logframe = ttk.LabelFrame(self.root, text="Activity log", padding=6)
        logframe.pack(fill="x", padx=12, pady=(0, 10))
        self.logbox = tk.Text(logframe, height=5, wrap="word", state="disabled")
        self.logbox.pack(fill="x")

        footer = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        footer.pack(fill="x")
        ttk.Label(footer, text=f"Reports: {REPORT_DIR}").pack(side="left")
        ttk.Label(footer, text=f"Quarantine: {QUARANTINE_DIR}").pack(side="right")

    def log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"[{stamp}] {text}\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def add(self, severity, category, name, details="", path="", pid=""):
        item = {
            "time": datetime.now(timezone.utc).isoformat(),
            "severity": severity, "category": category, "name": str(name),
            "details": str(details), "path": str(path), "pid": str(pid)
        }
        self.results.append(item)
        self.root.after(0, lambda: self.tree.insert("", "end",
                         values=(severity, category, name, details, path, pid),
                         tags=(severity,)))

    def clear_results(self):
        self.results.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def worker(self, label, function):
        if self.busy:
            messagebox.showinfo(APP_NAME, "A scan is already running.")
            return
        self.busy = True
        self.log(f"Starting: {label}")
        def task():
            try:
                function()
                self.root.after(0, lambda: self.log(f"Finished: {label}"))
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self.log(f"ERROR: {e}"))
                self.root.after(0, lambda e=str(exc): messagebox.showerror(APP_NAME, e))
            finally:
                self.root.after(0, lambda: setattr(self, "busy", False))
        threading.Thread(target=task, daemon=True).start()

    def full_hunt(self):
        self.clear_results()
        def work():
            self._process_network()
            self._persistence()
            self._defender()
            self._system_indicators()
            self.root.after(0, lambda: self.log(
                f"Hunt complete. {len(self.results)} observations recorded."))
        self.worker("Full defensive hunt", work)

    def process_network(self):
        self.clear_results()
        self.worker("Processes and network", self._process_network)

    def _process_network(self):
        self.root.after(0, lambda: self.log("Collecting processes..."))
        procs = ps_json(
            "Get-CimInstance Win32_Process | Select-Object Name,ProcessId,"
            "ParentProcessId,ExecutablePath,CommandLine")
        procmap = {}
        for p in procs:
            pid = str(p.get("ProcessId", ""))
            procmap[pid] = p
            path = p.get("ExecutablePath") or ""
            cmd = p.get("CommandLine") or ""
            lowpath = path.lower()
            suspicious_path = any(x in lowpath for x in
                                  (r"\appdata\local\temp\\", r"\downloads\\",
                                   r"\appdata\roaming\\"))
            suspicious_cmd = any(x in cmd.lower() for x in
                                 (" -enc ", " -encodedcommand ", "frombase64string",
                                  "downloadstring(", "invoke-expression"))
            if suspicious_path or suspicious_cmd:
                self.add("MEDIUM", "Process indicator", p.get("Name", ""),
                         "Writable/temp path or script-like command line; verify context.",
                         path or cmd, pid)
        self.root.after(0, lambda: self.log(f"Processes inspected: {len(procs)}"))

        conns = ps_json(
            "Get-NetTCPConnection | Select-Object LocalAddress,LocalPort,"
            "RemoteAddress,RemotePort,State,OwningProcess", timeout=60)
        for c in conns:
            pid = str(c.get("OwningProcess", ""))
            p = procmap.get(pid, {})
            pname = p.get("Name", "Unknown process")
            remote = c.get("RemoteAddress", "")
            state = c.get("State", "")
            # Do not label an IP/port as malicious. Record connections for triage.
            if state == "Established" and remote and remote not in ("0.0.0.0", "::", "127.0.0.1", "::1"):
                self.add("INFO", "Network connection", pname,
                         f"{c.get('LocalAddress')}:{c.get('LocalPort')} -> "
                         f"{remote}:{c.get('RemotePort')} ({state})",
                         p.get("ExecutablePath", ""), pid)
        self.root.after(0, lambda: self.log(f"TCP connection records: {len(conns)}"))

    def persistence_hunt(self):
        self.clear_results()
        self.worker("Persistence hunt", self._persistence)

    def _persistence(self):
        queries = [
            ("Startup", "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location,User"),
            ("Service", "Get-CimInstance Win32_Service | Select-Object Name,State,StartMode,PathName"),
            ("Scheduled task", "Get-ScheduledTask | Select-Object TaskName,TaskPath,State"),
            ("Driver", "Get-CimInstance Win32_SystemDriver | Select-Object Name,State,StartMode,PathName"),
        ]
        for category, query in queries:
            rows = ps_json(query, timeout=60)
            for row in rows:
                path = row.get("Command") or row.get("PathName") or ""
                low = path.lower()
                severity = "MEDIUM" if any(x in low for x in
                    (r"\appdata\local\temp", r"\appdata\roaming", r"\users\public")) else "INFO"
                name = row.get("Name") or row.get("TaskName") or "Unnamed"
                details = "; ".join(f"{k}={v}" for k, v in row.items()
                                    if k not in ("Name", "TaskName", "Command", "PathName"))
                self.add(severity, category, name, details, path)
            self.root.after(0, lambda c=category, n=len(rows):
                            self.log(f"{c} entries inspected: {n}"))
        # WMI subscriptions are reported as inventory, not automatically judged.
        for cls in ("__EventFilter", "__EventConsumer", "__FilterToConsumerBinding"):
            rows = ps_json(
                f"Get-CimInstance -Namespace root/subscription -ClassName {cls} "
                "| Select-Object *", timeout=30)
            for row in rows:
                self.add("MEDIUM", "WMI subscription", cls,
                         json.dumps(row, default=str)[:500], "")

    def defender_check(self):
        self.clear_results()
        self.worker("Microsoft Defender check", self._defender)

    def _defender(self):
        status = ps_json("Get-MpComputerStatus | Select-Object AntivirusEnabled,"
                         "RealTimeProtectionEnabled,BehaviorMonitorEnabled,"
                         "AntispywareEnabled,AntivirusSignatureLastUpdated")
        for row in status:
            for key, value in row.items():
                sev = "INFO"
                if key in ("AntivirusEnabled", "RealTimeProtectionEnabled",
                           "BehaviorMonitorEnabled", "AntispywareEnabled") and value is False:
                    sev = "HIGH"
                self.add(sev, "Defender status", key, str(value))
        threats = ps_json("Get-MpThreatDetection | Select-Object ThreatID,ThreatStatusID,"
                          "InitialDetectionTime,LastThreatStatusChangeTime,Resources")
        for t in threats:
            self.add("HIGH", "Defender detection", t.get("ThreatID", "Threat"),
                     json.dumps(t, default=str)[:700], str(t.get("Resources", "")))
        if not threats:
            self.add("INFO", "Defender detection", "No detection records returned",
                     "This does not prove the system is clean.")

    def _system_indicators(self):
        # DNS cache and proxy/hosts settings are collected as contextual evidence.
        # Use direct subprocess for text commands that do not emit JSON.
        proc = subprocess.run([PS, "-NoProfile", "-Command", "ipconfig /displaydns"],
                              capture_output=True, text=True, errors="replace", timeout=30)
        dns_text = proc.stdout[:200000]
        (REPORT_DIR / "DNS_cache.txt").write_text(dns_text, encoding="utf-8", errors="replace")
        hosts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/drivers/etc/hosts"
        if hosts.exists():
            self.add("INFO", "Hosts file", str(hosts),
                     "Review for unexpected domain-to-IP overrides.", str(hosts))
        proxy = subprocess.run([PS, "-NoProfile", "-Command", "netsh winhttp show proxy"],
                               capture_output=True, text=True, errors="replace", timeout=20)
        (REPORT_DIR / "Proxy_settings.txt").write_text(proxy.stdout + proxy.stderr,
                                                       encoding="utf-8", errors="replace")
        self.add("INFO", "Proxy configuration", "WinHTTP proxy",
                 proxy.stdout.strip()[:500])

    def file_scan(self):
        chosen = filedialog.askdirectory(title="Choose a folder to inspect")
        if not chosen:
            return
        self.worker("Selected folder scan", lambda: self._scan_folder(Path(chosen)))

    def _scan_folder(self, folder):
        self.root.after(0, lambda: self.log(f"Inspecting selected folder: {folder}"))
        count = 0
        max_files = 15000
        interesting = {".exe", ".dll", ".sys", ".scr", ".ps1", ".bat", ".cmd",
                       ".vbs", ".js", ".hta", ".jar", ".msi"}
        for current, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d.lower() not in
                       ("node_modules", ".git", "windowsapps")]
            for filename in files:
                if count >= max_files:
                    self.root.after(0, lambda: self.log("Safety limit reached: 15,000 files."))
                    return
                path = Path(current) / filename
                count += 1
                try:
                    stat = path.stat()
                    if path.suffix.lower() in interesting:
                        age_days = (time.time() - stat.st_mtime) / 86400
                        sev = "MEDIUM" if age_days < 14 else "INFO"
                        self.add(sev, "File inventory", filename,
                                 f"Extension={path.suffix}; Size={stat.st_size}; "
                                 f"Modified={datetime.fromtimestamp(stat.st_mtime)}",
                                 str(path))
                except (OSError, PermissionError):
                    continue
        self.root.after(0, lambda: self.log(f"Files inspected: {count}"))

    def selected_item(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select a result row first.")
            return None
        values = self.tree.item(selection[0], "values")
        return values

    def hash_file(self):
        chosen = filedialog.askopenfilename(title="Select a file to hash")
        if not chosen:
            return
        path = Path(chosen)
        def work():
            digest = sha256_file(path)
            self.add("INFO", "SHA-256", path.name, digest, str(path))
            self.root.after(0, lambda: self.log(f"SHA-256 calculated for {path}"))
        self.worker("File hashing", work)

    def quarantine_selected(self):
        values = self.selected_item()
        if not values:
            return
        path_text = values[4]
        if not path_text or not Path(path_text).is_file():
            messagebox.showwarning(APP_NAME, "Selected row does not identify an existing file.")
            return
        source = Path(path_text)
        if is_protected_path(source):
            messagebox.showerror(APP_NAME,
                "Safety block: files inside Windows and Program Files directories "
                "cannot be quarantined by this tool.")
            return
        if source.is_symlink():
            messagebox.showerror(APP_NAME, "Safety block: symbolic links are not quarantined.")
            return
        try:
            digest = sha256_file(source)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Cannot read file: {exc}")
            return
        destination = QUARANTINE_DIR / f"{digest}_{source.name}.quarantined"
        prompt = (
            "MANUAL QUARANTINE\n\n"
            f"File: {source}\nSHA-256: {digest}\n\n"
            "This moves the selected file into HORSEGRABBER's quarantine folder. "
            "It may break the associated application. Only proceed if you have "
            "verified this is unwanted or malicious. Continue?"
        )
        if not messagebox.askyesno(APP_NAME, prompt, icon="warning"):
            return
        try:
            shutil.move(str(source), str(destination))
            manifest = {
                "original_path": str(source), "quarantine_path": str(destination),
                "sha256": digest, "time": datetime.now(timezone.utc).isoformat(),
                "action": "manual_move"
            }
            with (QUARANTINE_DIR / "quarantine_manifest.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(manifest) + "\n")
            self.log(f"Quarantined selected file: {source}")
            messagebox.showinfo(APP_NAME, f"Moved to quarantine:\n{destination}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Quarantine failed: {exc}")

    def export_report(self):
        if not self.results:
            messagebox.showinfo(APP_NAME, "No findings to export. Run a scan first.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = REPORT_DIR / f"HorseGrabber_Report_{stamp}.json"
        csv_path = REPORT_DIR / f"HorseGrabber_Report_{stamp}.csv"
        html_path = REPORT_DIR / f"HorseGrabber_Report_{stamp}.html"
        metadata = {
            "tool": APP_NAME, "author": AUTHOR,
            "generated": datetime.now(timezone.utc).isoformat(),
            "notice": "Findings are indicators for human review, not proof of compromise.",
            "findings": self.results
        }
        json_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.results[0].keys()))
            writer.writeheader()
            writer.writerows(self.results)
        rows = "".join(
            "<tr>" + "".join(f"<td>{self._html(str(item.get(k,'')))}</td>"
                             for k in ("severity","category","name","details","path","pid"))
            + "</tr>" for item in self.results)
        html = f"""<!doctype html><html><head><meta charset="utf-8">
        <title>HORSEGRABBER Report</title>
        <style>body{{font:14px Segoe UI,Arial;margin:24px}}table{{border-collapse:collapse;width:100%}}
        th,td{{border:1px solid #bbb;padding:7px;text-align:left;overflow-wrap:anywhere}}
        th{{background:#eee}}.note{{padding:12px;background:#fff3cd}}</style></head><body>
        <h1>HORSEGRABBER Investigation Report</h1><p>Author: {AUTHOR}</p>
        <p>Generated: {metadata['generated']}</p>
        <p class="note">Indicators require human validation. This report does not identify an operator
        or establish a person's physical location.</p><table><tr><th>Severity</th><th>Category</th>
        <th>Name</th><th>Details</th><th>Path</th><th>PID</th></tr>{rows}</table></body></html>"""
        html_path.write_text(html, encoding="utf-8")
        self.log(f"Reports exported: {json_path}, {csv_path}, {html_path}")
        messagebox.showinfo(APP_NAME, f"Reports saved in:\n{REPORT_DIR}")

    @staticmethod
    def _html(value):
        return (value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if IS_WINDOWS else "clam")
    except tk.TclError:
        pass
    HorseGrabber(root)
    root.mainloop()

if __name__ == "__main__":
    main()

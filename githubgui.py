import os
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -----------------------------
# Helpers
# -----------------------------

def run_cmd(cmd, cwd, shell=False):
    """
    Run a command and return (exit_code, stdout+stderr).
    """
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            shell=shell,
            text=True,
            capture_output=True
        )
        out = ""
        if p.stdout:
            out += p.stdout
        if p.stderr:
            out += ("\n" if out else "") + p.stderr
        return p.returncode, out.strip()
    except Exception as e:
        return 999, f"Exception running command: {e}"

def looks_like_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))

def which_git():
    # Basic check that "git" is available
    code, out = run_cmd(["git", "--version"], cwd=os.getcwd())
    return code == 0, out

# -----------------------------
# UI App
# -----------------------------

class GitHelperUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Git Helper UI (Safe Buttons)")
        self.geometry("980x720")

        self.repo_path = tk.StringVar(value=os.getcwd())
        self.commit_msg = tk.StringVar(value="Update")
        self.tag_name = tk.StringVar(value="v1.0.0")

        self._build_ui()
        self._refresh_repo_status()

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Repo folder:").pack(side="left")
        self.repo_entry = ttk.Entry(top, textvariable=self.repo_path, width=70)
        self.repo_entry.pack(side="left", padx=8)

        ttk.Button(top, text="Browse…", command=self._browse_repo).pack(side="left")
        ttk.Button(top, text="Refresh", command=self._refresh_repo_status).pack(side="left", padx=(8, 0))

        # Repo info line
        info = ttk.Frame(self, padding=(10, 0, 10, 10))
        info.pack(fill="x")
        self.repo_info_label = ttk.Label(info, text="", foreground="#444")
        self.repo_info_label.pack(anchor="w")

        # Inputs
        inputs = ttk.Frame(self, padding=(10, 0, 10, 10))
        inputs.pack(fill="x")

        ttk.Label(inputs, text="Commit message:").grid(row=0, column=0, sticky="w")
        ttk.Entry(inputs, textvariable=self.commit_msg, width=60).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(inputs, text="Tag name:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(inputs, textvariable=self.tag_name, width=20).grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))

        # Main split: actions left, output right
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y")

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # Actions list
        ttk.Label(left, text="Actions").pack(anchor="w")

        self.actions = [
            {
                "name": "Status",
                "cmd": ["git", "status"],
                "desc": "Shows what files changed, what’s staged, and what’s untracked.",
            },
            {
                "name": "Diff (unstaged)",
                "cmd": ["git", "diff"],
                "desc": "Shows changes you made but haven’t staged yet.",
            },
            {
                "name": "Diff (staged)",
                "cmd": ["git", "diff", "--staged"],
                "desc": "Shows changes that are staged and will be included in the next commit.",
            },
            {
                "name": "Add all (stage)",
                "cmd": ["git", "add", "."],
                "desc": "Stages all changes in this folder (so they’ll be included in the commit).",
            },
            {
                "name": "Commit",
                "cmd": "COMMIT_DYNAMIC",
                "desc": "Creates a commit from staged changes (requires a commit message).",
            },
            {
                "name": "Pull (rebase)",
                "cmd": ["git", "pull", "--rebase"],
                "desc": "Gets latest from GitHub then re-applies your commits on top (clean history).",
            },
            {
                "name": "Push",
                "cmd": ["git", "push"],
                "desc": "Uploads your local commits to GitHub.",
            },
            {
                "name": "Log (last 20)",
                "cmd": ["git", "log", "--oneline", "--decorate", "-20"],
                "desc": "Shows the latest commits (short view).",
            },
            {
                "name": "Show remotes",
                "cmd": ["git", "remote", "-v"],
                "desc": "Shows where your repo pushes/pulls from (origin URL).",
            },
            {
                "name": "Tag version",
                "cmd": "TAG_DYNAMIC",
                "desc": "Creates a version tag (ex: v1.2.0) and pushes that tag to GitHub.",
            },
        ]

        self.action_list = tk.Listbox(left, height=16)
        self.action_list.pack(fill="y", expand=False, pady=(6, 6))
        for a in self.actions:
            self.action_list.insert(tk.END, a["name"])
        self.action_list.bind("<<ListboxSelect>>", self._on_action_select)

        ttk.Button(left, text="Run Selected", command=self._run_selected).pack(fill="x")
        ttk.Button(left, text="Clear Output", command=self._clear_output).pack(fill="x", pady=(6, 0))

        # Description box
        ttk.Label(left, text="What it does").pack(anchor="w", pady=(10, 0))
        self.desc = tk.Text(left, height=10, width=35, wrap="word")
        self.desc.pack(fill="both", expand=True, pady=(6, 0))
        self.desc.configure(state="disabled")

        # Output panel
        ttk.Label(right, text="Output").pack(anchor="w")
        self.output = tk.Text(right, wrap="none")
        self.output.pack(fill="both", expand=True, pady=(6, 0))

        # Scrollbars for output
        yscroll = ttk.Scrollbar(right, orient="vertical", command=self.output.yview)
        xscroll = ttk.Scrollbar(right, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")

        # Default selection
        self.action_list.selection_set(0)
        self._on_action_select()

    def _browse_repo(self):
        path = filedialog.askdirectory(initialdir=self.repo_path.get())
        if path:
            self.repo_path.set(path)
            self._refresh_repo_status()

    def _refresh_repo_status(self):
        ok_git, git_ver = which_git()
        path = self.repo_path.get().strip()

        if not ok_git:
            self.repo_info_label.config(text="Git not found. Install Git for Windows first (git-scm.com).")
            return

        if not os.path.isdir(path):
            self.repo_info_label.config(text="Folder does not exist.")
            return

        if looks_like_git_repo(path):
            # show branch too
            code, branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
            branch = branch if code == 0 else "(unknown branch)"
            self.repo_info_label.config(
                text=f"Git OK: {git_ver} | Repo: OK | Branch: {branch} | Path: {path}"
            )
        else:
            self.repo_info_label.config(
                text=f"Git OK: {git_ver} | Repo: NOT a git repo (no .git folder) | Path: {path}"
            )

    def _on_action_select(self, event=None):
        idxs = self.action_list.curselection()
        if not idxs:
            return
        idx = idxs[0]
        a = self.actions[idx]
        self._set_desc(a["desc"])

    def _set_desc(self, text):
        self.desc.configure(state="normal")
        self.desc.delete("1.0", tk.END)
        self.desc.insert("1.0", text)
        self.desc.configure(state="disabled")

    def _append_output(self, text):
        if not text:
            return
        self.output.insert(tk.END, text + "\n")
        self.output.see(tk.END)

    def _clear_output(self):
        self.output.delete("1.0", tk.END)

    def _run_selected(self):
        path = self.repo_path.get().strip()

        if not os.path.isdir(path):
            messagebox.showerror("Error", "Repo folder does not exist.")
            return

        if not looks_like_git_repo(path):
            messagebox.showwarning(
                "Not a git repo",
                "This folder is not a git repo (no .git). If this is a new project:\n\n"
                "1) git init\n2) git add .\n3) git commit -m \"Initial commit\"\n4) git remote add origin <url>\n5) git push -u origin main\n\n"
                "You can still run some commands, but most will fail."
            )

        idxs = self.action_list.curselection()
        if not idxs:
            return
        a = self.actions[idxs[0]]

        # Build dynamic commands
        if a["cmd"] == "COMMIT_DYNAMIC":
            msg = self.commit_msg.get().strip()
            if not msg:
                messagebox.showerror("Missing message", "Enter a commit message first.")
                return
            cmd = ["git", "commit", "-m", msg]
            label = f"$ git commit -m \"{msg}\""
        elif a["cmd"] == "TAG_DYNAMIC":
            tag = self.tag_name.get().strip()
            if not tag:
                messagebox.showerror("Missing tag", "Enter a tag name first (ex: v1.2.0).")
                return
            # We'll create tag then push it
            self._append_output(f"$ git tag {tag}")
            code1, out1 = run_cmd(["git", "tag", tag], cwd=path)
            self._append_output(out1 if out1 else "(no output)")
            if code1 != 0:
                self._append_output(f"(exit {code1})")
                return

            self._append_output(f"$ git push origin {tag}")
            code2, out2 = run_cmd(["git", "push", "origin", tag], cwd=path)
            self._append_output(out2 if out2 else "(no output)")
            self._append_output(f"(exit {code2})\n")
            return
        else:
            cmd = a["cmd"]
            label = "$ " + " ".join(cmd)

        self._append_output(label)
        code, out = run_cmd(cmd, cwd=path)
        self._append_output(out if out else "(no output)")
        self._append_output(f"(exit {code})\n")

if __name__ == "__main__":
    app = GitHelperUI()
    app.mainloop()

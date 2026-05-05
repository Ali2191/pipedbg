import * as vscode from "vscode";
import { execFile } from "child_process";
import { BreakpointManager } from "./breakpointManager";
import { PipelineProvider } from "./pipelineProvider";
import { StatusBar } from "./statusBar";
import { TerminalManager } from "./terminal";

export class DebugSession {
  private currentWorkflowPath: string | null = null;
  constructor(
    private terminalManager: TerminalManager,
    private statusBar: StatusBar,
    private pipelineProvider: PipelineProvider,
    private breakpointManager: BreakpointManager
  ) {
    this.terminalManager.onData((data) => this.handleTerminalData(data));
  }

  async runWorkflow(workflowPath: string, options: { dryRun?: boolean } = {}) {
    const config = vscode.workspace.getConfiguration("pipedbg");
    const pythonPath = config.get<string>("pythonPath", "python3");
    const envFile = config.get<string>("envFile", ".env");
    const dockerEnabled = config.get<boolean>("dockerEnabled", true);
    const autoOpenTerminal = config.get<boolean>("autoOpenTerminal", true);

    const breakpoints = this.breakpointManager.getBreakpoints(workflowPath);

    const args: string[] = ["-m", "pipedbg.cli", "run", workflowPath];
    if (options.dryRun) args.push("--dry-run");
    if (!dockerEnabled) args.push("--no-docker");
    if (envFile) args.push("--env-file", envFile);
    breakpoints.forEach((bp) => args.push("--break-on", bp));

    const cmd = `${pythonPath} ${args.map(quote).join(" ")}`;
    this.currentWorkflowPath = workflowPath;
    this.pipelineProvider.resetJobStatuses(workflowPath);
    this.statusBar.setRunning("starting");
    this.terminalManager.runCommand(cmd, autoOpenTerminal);
  }

  async inspectWorkflow(workflowPath: string) {
    const config = vscode.workspace.getConfiguration("pipedbg");
    const pythonPath = config.get<string>("pythonPath", "python3");
    const args = ["-m", "pipedbg.cli", "inspect", workflowPath];

    return new Promise<void>((resolve) => {
      execFile(pythonPath, args, { cwd: this.workspaceRoot() }, (err, stdout, stderr) => {
        const channel = vscode.window.createOutputChannel("pipedbg");
        channel.show(true);
        if (stderr) {
          channel.appendLine(stderr);
          this.showProMessageIfNeeded(stderr);
        }
        if (stdout) channel.appendLine(stdout);
        resolve();
      });
    });
  }

  async validateAll(workflows: string[]) {
    const config = vscode.workspace.getConfiguration("pipedbg");
    const pythonPath = config.get<string>("pythonPath", "python3");
    const channel = vscode.window.createOutputChannel("pipedbg");
    channel.show(true);

    for (const wf of workflows) {
      await new Promise<void>((resolve) => {
        execFile(pythonPath, ["-m", "pipedbg.cli", "validate", wf], { cwd: this.workspaceRoot() }, (err, stdout, stderr) => {
          if (stderr) {
            channel.appendLine(stderr);
            this.showProMessageIfNeeded(stderr);
          }
          if (stdout) channel.appendLine(stdout);
          resolve();
        });
      });
    }
  }

  private handleTerminalData(data: string) {
    const text = stripAnsi(data).trim();
    if (!text) return;

    if (text.includes("BREAKPOINT")) {
      this.terminalManager.focus();
    }

    if (text.includes("Pro Feature")) {
      this.showProPrompt();
    }

    const jobStart = text.match(/^[\-─]{2,}\s*(.+?)\s{2,}/);
    if (jobStart) {
      const jobName = jobStart[1].trim();
      this.statusBar.setRunning(jobName);
      if (this.currentWorkflowPath) {
        this.pipelineProvider.updateJobStatusByName(this.currentWorkflowPath, jobName, "running");
      }
    }

    const jobResult = text.match(/^[✓✗○–]?\s*(.+?)\s+(SUCCESS|FAILED|SKIPPED)/i);
    if (jobResult) {
      const jobName = jobResult[1].trim();
      const status = jobResult[2].toLowerCase();
      if (this.currentWorkflowPath) {
        const mapped = status === "success" ? "passed" : status === "failed" ? "failed" : "pending";
        this.pipelineProvider.updateJobStatusByName(this.currentWorkflowPath, jobName, mapped);
      }
    }

    if (text.includes("PASSED")) {
      this.statusBar.setPassed();
    }
    if (text.includes("FAILED")) {
      this.statusBar.setFailed();
    }
  }

  private showProMessageIfNeeded(stderr: string) {
    if (stderr.includes("Pro Feature")) {
      this.showProPrompt();
    }
  }

  private showProPrompt() {
    vscode.window.showInformationMessage(
      "This feature requires pipedbg Pro.",
      "Upgrade"
    ).then((choice) => {
      if (choice === "Upgrade") {
        vscode.env.openExternal(vscode.Uri.parse("https://pipedbg.dev/pro"));
      }
    });
  }

  private workspaceRoot(): string | undefined {
    const root = vscode.workspace.workspaceFolders?.[0];
    return root?.uri.fsPath;
  }
}

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, "");
}

function quote(value: string): string {
  if (value.includes(" ")) return `"${value}"`;
  return value;
}

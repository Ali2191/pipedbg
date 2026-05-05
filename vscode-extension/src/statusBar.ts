import * as vscode from "vscode";

export class StatusBar {
  private item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.item.text = "pipedbg: idle";
    this.item.command = "pipedbg.openTerminal";
    this.item.show();
  }

  setIdle() {
    this.item.text = "pipedbg: idle";
  }

  setRunning(jobName: string) {
    this.item.text = `⟳ pipedbg: running ${jobName}`;
  }

  setPassed() {
    this.item.text = "✓ pipedbg: passed";
  }

  setFailed() {
    this.item.text = "✗ pipedbg: failed";
  }

  dispose() {
    this.item.dispose();
  }
}

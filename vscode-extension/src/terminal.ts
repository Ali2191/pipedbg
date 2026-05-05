import * as vscode from "vscode";

export class TerminalManager {
  private terminal: vscode.Terminal | null = null;
  private dataEmitter = new vscode.EventEmitter<string>();
  public readonly onData = this.dataEmitter.event;

  constructor() {
    const onDidWriteTerminalData = (vscode.window as any).onDidWriteTerminalData;
    if (typeof onDidWriteTerminalData === "function") {
      onDidWriteTerminalData((e: any) => {
        if (this.terminal && e.terminal === this.terminal) {
          this.dataEmitter.fire(e.data);
        }
      });
    }
  }

  getOrCreateTerminal(): vscode.Terminal {
    if (!this.terminal) {
      this.terminal = vscode.window.createTerminal({ name: "pipedbg" });
    }
    return this.terminal;
  }

  runCommand(command: string, focus: boolean) {
    const term = this.getOrCreateTerminal();
    term.sendText(command, true);
    if (focus) {
      term.show();
    }
  }

  focus() {
    if (this.terminal) {
      this.terminal.show();
    }
  }
}

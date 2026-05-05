import * as vscode from "vscode";
import { execFile } from "child_process";
import { BreakpointManager } from "./breakpointManager";
import { DebugSession } from "./debugSession";
import { PipelineProvider } from "./pipelineProvider";
import { StatusBar } from "./statusBar";
import { TerminalManager } from "./terminal";

class NoopDebugAdapter implements vscode.DebugAdapter {
  private _onDidSendMessage = new vscode.EventEmitter<any>();
  readonly onDidSendMessage = this._onDidSendMessage.event;

  handleMessage(): void {
    return;
  }

  dispose(): void {
    this._onDidSendMessage.dispose();
  }
}

export function activate(context: vscode.ExtensionContext) {
  const inspectJson = async (workflowPath: string): Promise<any | null> => {
    const config = vscode.workspace.getConfiguration("pipedbg");
    const pythonPath = config.get<string>("pythonPath", "python3");
    const args = ["-m", "pipedbg.cli", "inspect", workflowPath, "--json"];

    return new Promise((resolve) => {
      execFile(pythonPath, args, { cwd: workspaceRoot() }, (err, stdout) => {
        if (err) {
          resolve(null);
          return;
        }
        try {
          const data = JSON.parse(stdout);
          resolve({
            path: workflowPath,
            name: data.name || workflowPath,
            jobs: data.jobs || {},
          });
        } catch (e) {
          resolve(null);
        }
      });
    });
  };

  const breakpointManager = new BreakpointManager(context, inspectJson);
  const pipelineProvider = new PipelineProvider(breakpointManager, inspectJson);
  const statusBar = new StatusBar();
  const terminalManager = new TerminalManager();
  const debugSession = new DebugSession(terminalManager, statusBar, pipelineProvider, breakpointManager);

  const treeView = vscode.window.createTreeView("pipedbg", { treeDataProvider: pipelineProvider });

  context.subscriptions.push(treeView);

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.runWorkflow", async () => {
      const workflows = await pipelineProvider.loadWorkflows();
      if (!workflows.length) {
        vscode.window.showWarningMessage("No workflows found in .github/workflows");
        return;
      }
      const pick = await vscode.window.showQuickPick(
        workflows.map((wf) => ({ label: wf.name, description: wf.path })),
        { placeHolder: "Select a workflow to run" }
      );
      if (!pick) return;
      await debugSession.runWorkflow(pick.description || pick.label);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.runCurrentWorkflow", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      await debugSession.runWorkflow(editor.document.uri.fsPath);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.dryRun", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      await debugSession.runWorkflow(editor.document.uri.fsPath, { dryRun: true });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.inspectWorkflow", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      await debugSession.inspectWorkflow(editor.document.uri.fsPath);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.validateAllWorkflows", async () => {
      const workflows = await pipelineProvider.loadWorkflows();
      await debugSession.validateAll(workflows.map((wf) => wf.path));
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("pipedbg.openTerminal", () => {
      terminalManager.focus();
    })
  );

  // Register a noop debug adapter descriptor (required by spec).
  const factory: vscode.DebugAdapterDescriptorFactory = {
    createDebugAdapterDescriptor: () => {
      return new vscode.DebugAdapterInlineImplementation(new NoopDebugAdapter());
    },
  };
  context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory("pipedbg", factory));

  // Refresh tree on save
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(() => pipelineProvider.refresh())
  );
}

export function deactivate() {}

function workspaceRoot(): string | undefined {
  const root = vscode.workspace.workspaceFolders?.[0];
  return root?.uri.fsPath;
}

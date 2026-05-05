import * as path from "path";
import * as vscode from "vscode";

export interface StepJson {
  id: string;
  name: string;
  run?: string | null;
}

export interface WorkflowJson {
  jobs: Record<string, { id: string; name: string; steps: StepJson[] }>;
}

export class BreakpointManager {
  private decorationType: vscode.TextEditorDecorationType;
  private breakpoints: Map<string, Set<string>> = new Map();
  private stepLineMap: Map<string, Map<string, number>> = new Map();
  private lineStepMap: Map<string, Map<number, string>> = new Map();

  constructor(
    private context: vscode.ExtensionContext,
    private getInspectJson: (workflowPath: string) => Promise<WorkflowJson | null>
  ) {
    const iconPath = context.asAbsolutePath(path.join("media", "breakpoint.svg"));
    this.decorationType = vscode.window.createTextEditorDecorationType({
      gutterIconPath: iconPath,
      gutterIconSize: "16px",
    });

    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        this.refresh(editor);
      }
    });

    vscode.workspace.onDidChangeTextDocument((e) => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === e.document) {
        this.refresh(editor);
      }
    });

    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (!this.isWorkflowDoc(e.textEditor.document)) {
        return;
      }
      if (e.kind === vscode.TextEditorSelectionChangeKind.Mouse) {
        const line = e.selections[0].active.line;
        const char = e.selections[0].active.character;
        if (char === 0) {
          this.toggleBreakpoint(e.textEditor, line);
        }
      }
    });
  }

  async refresh(editor: vscode.TextEditor) {
    if (!this.isWorkflowDoc(editor.document)) {
      editor.setDecorations(this.decorationType, []);
      return;
    }

    await this.updateMapping(editor.document);
    this.applyDecorations(editor);
  }

  async toggleBreakpoint(editor: vscode.TextEditor, line: number) {
    if (!this.isWorkflowDoc(editor.document)) {
      return;
    }

    await this.updateMapping(editor.document);
    const workflowPath = editor.document.uri.fsPath;
    const lineMap = this.lineStepMap.get(workflowPath);
    if (!lineMap) {
      return;
    }

    const stepId = lineMap.get(line);
    if (!stepId) {
      return;
    }

    const set = this.breakpoints.get(workflowPath) || new Set();
    if (set.has(stepId)) {
      set.delete(stepId);
    } else {
      set.add(stepId);
    }
    this.breakpoints.set(workflowPath, set);
    this.applyDecorations(editor);
  }

  hasBreakpoint(workflowPath: string, stepId: string): boolean {
    const set = this.breakpoints.get(workflowPath);
    return set ? set.has(stepId) : false;
  }

  getBreakpoints(workflowPath: string): string[] {
    return Array.from(this.breakpoints.get(workflowPath) || []);
  }

  private applyDecorations(editor: vscode.TextEditor) {
    const workflowPath = editor.document.uri.fsPath;
    const stepMap = this.stepLineMap.get(workflowPath) || new Map();
    const breakSet = this.breakpoints.get(workflowPath) || new Set();
    const ranges: vscode.DecorationOptions[] = [];

    for (const [stepId, line] of stepMap.entries()) {
      if (breakSet.has(stepId)) {
        const range = new vscode.Range(line, 0, line, 0);
        ranges.push({ range });
      }
    }
    editor.setDecorations(this.decorationType, ranges);
  }

  private async updateMapping(document: vscode.TextDocument) {
    const workflowPath = document.uri.fsPath;
    const runLines = this.getRunLines(document);

    const inspect = await this.getInspectJson(workflowPath);
    if (!inspect) {
      return;
    }

    const runSteps: StepJson[] = [];
    Object.values(inspect.jobs).forEach((job) => {
      job.steps.forEach((step) => {
        if (step.run) {
          runSteps.push(step);
        }
      });
    });

    const stepToLine = new Map<string, number>();
    const lineToStep = new Map<number, string>();
    const count = Math.min(runSteps.length, runLines.length);
    for (let i = 0; i < count; i++) {
      stepToLine.set(runSteps[i].id, runLines[i]);
      lineToStep.set(runLines[i], runSteps[i].id);
    }

    this.stepLineMap.set(workflowPath, stepToLine);
    this.lineStepMap.set(workflowPath, lineToStep);
  }

  private getRunLines(document: vscode.TextDocument): number[] {
    const lines: number[] = [];
    for (let i = 0; i < document.lineCount; i++) {
      const text = document.lineAt(i).text;
      if (/^\s*-?\s*run\s*:/i.test(text)) {
        lines.push(i);
      }
    }
    return lines;
  }

  private isWorkflowDoc(document: vscode.TextDocument): boolean {
    const file = document.uri.fsPath;
    return (
      (file.endsWith(".yml") || file.endsWith(".yaml")) &&
      file.includes(`${path.sep}.github${path.sep}workflows${path.sep}`)
    );
  }
}

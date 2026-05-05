import * as vscode from "vscode";
import { BreakpointManager, WorkflowJson } from "./breakpointManager";

interface WorkflowModel extends WorkflowJson {
  path: string;
  name: string;
}

type JobStatus = "pending" | "running" | "passed" | "failed";

type InspectJsonFn = (workflowPath: string) => Promise<WorkflowModel | null>;

export class PipelineProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private workflowCache: Map<string, WorkflowModel> = new Map();
  private jobStatus: Map<string, Map<string, JobStatus>> = new Map();

  constructor(private breakpointManager: BreakpointManager, private inspectJson: InspectJsonFn) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  async getChildren(element?: vscode.TreeItem): Promise<vscode.TreeItem[]> {
    if (!element) {
      const workflows = await this.loadWorkflows();
      return workflows.map((wf) => new WorkflowItem(wf));
    }

    if (element instanceof WorkflowItem) {
      const wf = element.workflow;
      return Object.values(wf.jobs).map((job) => {
        const status = this.getJobStatus(wf.path, job.id);
        return new JobItem(wf, job.id, job.name, status);
      });
    }

    if (element instanceof JobItem) {
      const wf = element.workflow;
      const job = wf.jobs[element.jobId];
      return job.steps.map((step) => {
        const hasBp = this.breakpointManager.hasBreakpoint(wf.path, step.id);
        return new StepItem(step.name, hasBp);
      });
    }

    return [];
  }

  getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  async loadWorkflows(): Promise<WorkflowModel[]> {
    const files = await vscode.workspace.findFiles(".github/workflows/*.{yml,yaml}");
    const results: WorkflowModel[] = [];

    for (const file of files) {
      const wf = await this.inspectJson(file.fsPath);
      if (wf) {
        this.workflowCache.set(file.fsPath, wf);
        results.push(wf);
      }
    }
    return results;
  }

  updateJobStatus(workflowPath: string, jobId: string, status: JobStatus) {
    const map = this.jobStatus.get(workflowPath) || new Map();
    map.set(jobId, status);
    this.jobStatus.set(workflowPath, map);
    this.refresh();
  }

  updateJobStatusByName(workflowPath: string, jobName: string, status: JobStatus) {
    const wf = this.workflowCache.get(workflowPath);
    if (!wf) return;
    const jobEntry = Object.values(wf.jobs).find((j) => j.name === jobName || j.id === jobName);
    if (!jobEntry) return;
    this.updateJobStatus(workflowPath, jobEntry.id, status);
  }

  resetJobStatuses(workflowPath: string) {
    this.jobStatus.set(workflowPath, new Map());
    this.refresh();
  }

  private getJobStatus(workflowPath: string, jobId: string): JobStatus {
    const map = this.jobStatus.get(workflowPath);
    return map?.get(jobId) || "pending";
  }

}

class WorkflowItem extends vscode.TreeItem {
  constructor(public workflow: WorkflowModel) {
    super(workflow.name, vscode.TreeItemCollapsibleState.Collapsed);
    this.description = workflow.path;
    this.contextValue = "workflow";
    this.iconPath = new vscode.ThemeIcon("repo");
  }
}

class JobItem extends vscode.TreeItem {
  constructor(
    public workflow: WorkflowModel,
    public jobId: string,
    name: string,
    status: JobStatus
  ) {
    super(name, vscode.TreeItemCollapsibleState.Collapsed);
    this.contextValue = "job";
    this.iconPath = this.iconForStatus(status);
  }

  private iconForStatus(status: JobStatus): vscode.ThemeIcon {
    switch (status) {
      case "running":
        return new vscode.ThemeIcon("sync");
      case "passed":
        return new vscode.ThemeIcon("check");
      case "failed":
        return new vscode.ThemeIcon("error");
      default:
        return new vscode.ThemeIcon("circle-outline");
    }
  }
}

class StepItem extends vscode.TreeItem {
  constructor(name: string, hasBreakpoint: boolean) {
    super(name, vscode.TreeItemCollapsibleState.None);
    this.contextValue = "step";
    this.iconPath = hasBreakpoint ? new vscode.ThemeIcon("debug-breakpoint") : new vscode.ThemeIcon("circle-outline");
  }
}

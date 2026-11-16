export interface FirstRunResult {
  status: 'running' | 'passed' | 'partial' | 'failed' | 'interrupted' | 'skipped';
  message: string;
  measured_calls?: number;
  configured_calls?: number;
  recommended_calls?: number;
  kind?: string;
  cycle_calls?: number;
  preview?: string;
  adapter_id?:string;
  generation?:number;
  confirmed?:boolean;
  proposed_actions?:{operation:string;label?:string;arguments:Record<string,unknown>;checks?:string[];not_verified?:string[]}[];
  unexercised_actions?:string[];
  previewed_action_count?:number;
  resource_checks?:Record<string,{resource:string;item_count:number;supplied_item_count?:number;message_bodies_read?:number}[]>;
  /** "sample" when the person's own data gave it nothing to do, and Mavo tested it with a
   *  sample it wrote: the sample, and a line saying so. */
  tested_with?:'sample';
  phase?:string;
  sample?:Record<string,Record<string,unknown>[]>;
  sample_note?:string;
  /** A workflow's scenarios (kind "workflow"): each, and what it did wrong. */
  done?:number;
  passed_count?:number;
  accepted_with?:string[];
  total?:number;
  round?:number;
  rounds?:number;
  tests?:{name:string;passed:boolean;problems:string[];email?:string;expected?:string[];did?:string[];stopped?:string[]}[];
}

export function firstRunResult(plan: unknown): FirstRunResult | undefined {
  if (!plan || typeof plan !== 'object' || !('first_run' in plan)) return;
  const value = plan.first_run;
  if (!value || typeof value !== 'object' || !('status' in value)) return;
  return value as FirstRunResult;
}

export function firstRunLabel(result?: FirstRunResult): string {
  if (!result) return 'Test it';
  if (result.status === 'running' && result.phase === 'fixing') return `Fixing what failed, round ${result.round ?? 1} of ${result.rounds ?? 2}…`;
  if (result.status === 'running') return result.total ? `${result.round ? 'Testing again' : 'Testing'}… ${result.done ?? 0} of ${result.total}` : 'Testing…';
  if (result.status !== 'passed') return 'Test again';
  return 'Turn it on';
}

/** The test's outcome in a few words. A failure keeps its reason: that is what to fix. */
export function firstRunLine(result?: FirstRunResult): string | null {
  if (!result) return null;
  if (result.status === 'running' && result.phase === 'fixing') return `Fixing what failed, round ${result.round ?? 1} of ${result.rounds ?? 2}…`;
  if (result.status === 'running') return result.total ? `${result.round ? 'Testing again' : 'Testing'}… ${result.done ?? 0} of ${result.total}` : 'Testing…';
  if (result.status === 'interrupted') return 'The test stopped. Test it again.';
  if (result.status === 'skipped') return 'Turned on without a test.';
  // A path's verdict, in plain words: all passed, most passed, or not recommended yet.
  if (result.kind === 'workflow') return result.message || null;
  if (result.status === 'failed') return null;
  if (result.kind === 'action_preview') return null;
  if (result.kind === 'workflow') return result.message || 'Test passed.';
  return 'Test passed.';
}

const TEST_GATES=new Set(['Complete first-run verification before adding this Crew','Review setup and select Verify & add','Review the test preview before enabling changes']);
export function setupBlockers(missing?:string[]):string[]{return (missing??[]).filter(item=>!TEST_GATES.has(item));}

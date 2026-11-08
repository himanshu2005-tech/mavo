/// <reference types="node" />
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FirebaseLoginCard } from "./components/FirebaseLoginCard";
import { ResourcesPage } from "./pages/ResourcesPage";
import { DeleteCrewDialog } from "./components/DeleteCrewDialog";
import { CrewSetupCard } from "./components/CrewSetupCard";
import { BrowserSignIn } from "./components/BrowserSignIn";
import { DesktopUpdateCard, ago } from "./components/DesktopUpdateCard";
import { ConnectorPermissions } from "./components/ConnectorAccounts";
import { GitHubSignIn } from "./components/GitHubSignIn";
import type { CrewInstance } from "./types";

describe("Mavo dark presentation", () => {
  it("shows an optional GitHub connection honestly without granting actions",()=>{
    const html=renderToStaticMarkup(<GitHubSignIn provider={{provider:"github",name:"GitHub",configured:true,connected:false,email:null,capabilities:[],available_capabilities:["github:repo:read"]}} onChanged={()=>{}}/>);
    expect(html).toContain("Checking");expect(html).toContain("Connect GitHub");expect(html).not.toContain("Send email");
    expect(html).not.toMatch(/Fine-grained token|Advanced token|Save connection/);
  });
  it("shows Connected and the actual granted capabilities for a connected GitHub account",()=>{
    const html=renderToStaticMarkup(<GitHubSignIn provider={{provider:"github",name:"GitHub",configured:true,connected:true,email:"fixture",capabilities:["github:repo:read"],available_capabilities:[],repositories:["fixture/project"]}} onChanged={()=>{}}/>);
    expect(html).toContain('aria-label="GitHub connection status">Connected');
    expect(html).toContain("Revoke access");expect(html).toContain("fixture/project");
    expect(html).not.toContain("Create issues and comments");
  });
  it("shows only the exact proposed connector capabilities",()=>{
    const html=renderToStaticMarkup(<ConnectorPermissions value={[]} onChange={()=>{}} allowed={["github:repo:read"]}/>);
    expect(html).toContain("Read selected repositories");expect(html).not.toMatch(/Gmail|Microsoft|Create issues/);
  });
  it("offers separate read-only GitHub pull-request and CI permissions",()=>{
    const html=renderToStaticMarkup(<ConnectorPermissions value={[]} onChange={()=>{}} allowed={["github:pulls:read","github:workflows:read"]}/>);
    expect(html).toContain("Read pull requests");expect(html).toContain("Read workflow and CI status");
    expect(html).not.toMatch(/Create issues|Gmail|Send email/);
    expect(html).not.toContain('checked=""');
  });
  it("shows a Crew message plainly, with a way to ask about it and nothing destructive",()=>{
    const noop=()=>{};
    const view=renderToStaticMarkup(<DesktopUpdateCard reminder={{id:"test",kind:"crew_update",crew_id:"crew",crew_name:"C Drive",message:"12.50 GB free",scheduled_for:"2026-11-02T12:00:00Z",local_date:"2026-11-02"}} busy={false} onDismiss={noop} onOpen={noop} onClose={noop} onSpeak={noop}/>);
    expect(view).toContain("C Drive");expect(view).toContain("12.50 GB free");
    expect(view).toContain("Ask");expect(view).toContain("Open");expect(view).toContain("Done");
    expect(view).not.toMatch(/New result|Crew update|Delete files|Clean drive/);
  });
  it("says how long ago at a glance",()=>{
    const now=Date.parse("2026-11-05T12:00:00Z");
    expect(ago("2026-11-05T11:59:40Z",now)).toBe("now");
    expect(ago("2026-11-05T11:55:00Z",now)).toBe("5m");
    expect(ago("2026-11-05T09:00:00Z",now)).toBe("3h");
  });
  it("uses a direct separate-browser sign-in without requesting password files",()=>{
    const view=renderToStaticMarkup(<BrowserSignIn crew={{id:"test",name:"Portal",status:"active",responsibilities:"Read https://example.com"}}/>);
    expect(view).toContain("Open browser to sign in");
    expect(view).toContain('value="https://example.com"');
    expect(view).not.toMatch(/type="(?:file|password)"/);
  });
  it.each([false,true])("shows access settings only after the questionnaire is finished (%s)", (answered) => {
    const draft={id:"fixture",name:"Portal Watch",status:"draft",permissions:["model:invoke"],
      route_id:null,is_running:false,responsibilities:"Read listings every 30 minutes.",
      configuration_plan:{phase:answered?"needs_access":"needs_input",questions:answered?[]:["What is the portal URL?"],draft:{
        name:"Portal Watch",template_id:"operator",responsibilities:"Read listings",trigger_type:"interval",
        requested_google_capabilities:["gmail:read"],requested_google_resources:[],
        requested_actions:[{operation:"browser.read",reason:"Read listings",folders:[],domains:[],apps:[],recipients:[]}],
        requested_notifications:true,requested_device_metrics:false,requested_weather:false,
        daily_call_limit:48,max_model_calls:5,
      }}} as unknown as CrewInstance;
    const card=renderToStaticMarkup(<CrewSetupCard crew={draft}/>);
    if(answered) {
      expect(card).toContain("Agent connection");
      expect(card).toContain("Resource access");
      expect(card).toContain("Read inbox headers");
      expect(card).toContain("Add Crew");
      expect(card).not.toContain("Show important updates");
    } else {
      expect(card).toContain("What is the portal URL?");
      expect(card).toContain("Connections and permissions come next.");
      expect(card).not.toMatch(/Agent connection|Resource access|Read inbox headers|Add Crew|Show important updates|browser/);
    }
  });
  it("offers explicit deletion and cancellation even for a running Crew", () => {
    const crew={id:"fixture",name:"Test Crew",status:"active",is_running:true} as CrewInstance;
    const dialog=renderToStaticMarkup(<DeleteCrewDialog crew={crew} onClose={()=>{}}/>);
    expect(dialog).toContain('role="alertdialog"');
    expect(dialog).toContain("Delete Crew");
    expect(dialog).toContain("Cancel");
    expect(dialog).toContain("Stops this Crew");
    expect(dialog).not.toContain("disabled");
  });
  it("keeps computer location automatic without manual location controls", () => {
    const resources = renderToStaticMarkup(<ResourcesPage />);
    expect(resources).not.toMatch(/Set location|Detect location|Weather city|Use location/);
    expect(resources).toContain("automatically");
    expect(resources).toContain('aria-label="Location access"');
  });
  it("keeps login concise without implementation details", () => {
    const login = renderToStaticMarkup(<FirebaseLoginCard />);
    expect(login).toContain("Sign in");
    expect(login).not.toMatch(/Firebase|powered by|publisher/i);
  });

  it("has a Light and a Dark theme that share one set of colour names", () => {
    const config = readFileSync(new URL("../tailwind.config.js", import.meta.url), "utf8");
    // Tailwind colours read the theme variables, so every class works in both themes.
    expect(config).toContain('ink: "var(--background)"');
    expect(config).toContain('panel: "var(--surface)"');
    expect(config).toContain('50: "var(--z50)"');
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    expect(styles).toContain("color-scheme: dark");
    expect(styles).toContain("color-scheme: light");
    const light = styles.slice(styles.indexOf(":root[data-theme=light]"));
    for (const name of ["--background", "--surface", "--text", "--muted", "--border", "--accent", "--z50", "--z900"]) {
      expect(styles.indexOf(name + ":"), name + " in the dark theme").toBeGreaterThan(-1);
      expect(light.indexOf(name + ":"), name + " in the light theme").toBeGreaterThan(-1);
    }
    // Neither theme paints a page in pure black or pure white.
    expect(styles).not.toMatch(/--background:\s*(?:#000000?|#ffffff?)\s*;/i);
    expect(styles).not.toContain(".app-shell .text-white");
  });
});

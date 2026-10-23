import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { yaml } from "@codemirror/lang-yaml";
import { lintGutter, setDiagnostics, type Diagnostic as CMDiag } from "@codemirror/lint";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";
import type { Diagnostic } from "../lib/api";
import { mysyaCompletion, mysyaLanguage } from "../lib/mysyaLang";

export function languageFor(path: string): Extension {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "mysya":
      return [mysyaLanguage, mysyaCompletion];
    case "yml":
    case "yaml":
    case "driver":
      return yaml();
    case "html":
    case "htm":
      return html();
    case "css":
      return css();
    case "js":
    case "mjs":
    case "jsx":
      return javascript({ jsx: true });
    case "ts":
    case "tsx":
      return javascript({ jsx: true, typescript: true });
    case "json":
      return javascript();
    case "py":
      return python();
    default:
      return [];
  }
}

const transparent = EditorView.theme({
  "&": { backgroundColor: "transparent", height: "100%" },
  ".cm-gutters": { backgroundColor: "transparent", borderRight: "1px solid var(--panel-border)" },
  ".cm-scroller": { fontFamily: "var(--font-mono)" },
});

type Props = {
  path: string;
  value: string;
  onChange: (v: string) => void;
  onSave?: () => void;
  onRun?: () => void;
  diagnostics?: Diagnostic[];
  dark?: boolean;
};

export function Editor({ path, value, onChange, onSave, onRun, diagnostics, dark = true }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());
  const theme = useRef(new Compartment());
  const cb = useRef({ onChange, onSave, onRun });
  cb.current = { onChange, onSave, onRun };

  useEffect(() => {
    const state = EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
        lintGutter(),
        lang.current.of(languageFor(path)),
        theme.current.of(dark ? oneDark : []),
        transparent,
        EditorView.lineWrapping,
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => (cb.current.onSave?.(), true) },
          { key: "Mod-Enter", preventDefault: true, run: () => (cb.current.onRun?.(), true) },
          { key: "F5", preventDefault: true, run: () => (cb.current.onRun?.(), true) },
        ]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) cb.current.onChange(u.state.doc.toString());
        }),
      ],
    });
    view.current = new EditorView({ state, parent: host.current! });
    return () => view.current?.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // New file opened: swap document and language.
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    v.dispatch({ effects: lang.current.reconfigure(languageFor(path)) });
  }, [path]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value, path]);

  useEffect(() => {
    view.current?.dispatch({ effects: theme.current.reconfigure(dark ? oneDark : []) });
  }, [dark]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const doc = v.state.doc;
    const diags: CMDiag[] = (diagnostics ?? []).map((d) => {
      const line = doc.line(Math.min(Math.max(d.line, 1), doc.lines));
      const from = Math.min(line.from + Math.max(d.column - 1, 0), line.to);
      return { from, to: Math.max(from + 1, Math.min(line.to, from + 1)), severity: d.severity, message: d.message };
    });
    v.dispatch(setDiagnostics(v.state, diags));
  }, [diagnostics]);

  return <div ref={host} className="h-full min-h-0 overflow-hidden" data-testid="code-editor" />;
}

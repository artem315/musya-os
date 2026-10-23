// CodeMirror support for MUSYA (.mysya): highlighting + autocompletion.
import { StreamLanguage, type StreamParser } from "@codemirror/language";
import { autocompletion, snippetCompletion, type CompletionContext } from "@codemirror/autocomplete";

export const KEYWORDS = [
  "app", "let", "const", "if", "elif", "else", "end", "while", "for", "in", "func", "return", "break", "continue",
  "true", "false", "null", "and", "or", "not", "service", "print", "try", "catch", "finally", "throw", "import",
  "as", "on", "emit", "spawn", "await", "assert", "test", "async",
];
export const SERVICE_VERBS = [
  "start", "stop", "status", "load", "search", "click", "type", "press", "scroll", "wait", "screenshot", "download",
  "event", "ai", "back", "forward", "reload", "tab", "exists", "text", "url", "title", "html", "errors", "fill",
  "select", "hover", "dblclick", "click_at", "install",
];
export const BUILTINS = ["len", "str", "num", "int", "float", "bool", "type", "range", "keys", "values", "sleep", "now",
  "min", "max", "abs", "round", "error", "done", "math", "json", "file", "ai"];

type State = { inString: false | '"' | "'" | '"""' };

const parser: StreamParser<State> = {
  name: "mysya",
  startState: () => ({ inString: false }),
  token(stream, state) {
    if (state.inString) {
      const q = state.inString;
      while (!stream.eol()) {
        if (stream.match("${", false)) {
          if (stream.pos === stream.start) {
            stream.match("${");
            stream.skipTo("}");
            stream.next();
            return "variableName.special";
          }
          return "string";
        }
        if (stream.match(q)) {
          state.inString = false;
          return "string";
        }
        if (stream.next() === "\\") stream.next();
      }
      if (q !== '"""') state.inString = false;
      return "string";
    }
    if (stream.eatSpace()) return null;
    if (stream.match("#") || stream.match("//")) {
      stream.skipToEnd();
      return "comment";
    }
    if (stream.match('"""')) {
      state.inString = '"""';
      return "string";
    }
    const ch = stream.peek();
    if (ch === '"' || ch === "'") {
      stream.next();
      state.inString = ch;
      return "string";
    }
    if (stream.match(/^\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/)) return "number";
    if (stream.match(/^[A-Za-z_][\w]*/)) {
      const word = stream.current();
      if (word === "service") return "keyword";
      if (KEYWORDS.includes(word)) return ["true", "false", "null"].includes(word) ? "atom" : "keyword";
      if (BUILTINS.includes(word)) return "variableName.standard";
      if (stream.match(/^\s*\(/, false)) return "function";
      return "variableName";
    }
    if (stream.match(/^(==|!=|<=|>=|\*\*|=>|[+\-*/%<>=])/)) return "operator";
    stream.next();
    return "punctuation";
  },
  languageData: { commentTokens: { line: "#" }, closeBrackets: { brackets: ["(", "[", "{", '"', "'"] } },
};

export const mysyaLanguage = StreamLanguage.define(parser);

const SNIPPETS = [
  snippetCompletion("if ${cond}:\n    ${}\nend", { label: "if", detail: "block", type: "keyword" }),
  snippetCompletion("for ${item} in ${list}:\n    ${}\nend", { label: "for", detail: "loop", type: "keyword" }),
  snippetCompletion("while ${cond}:\n    ${}\nend", { label: "while", detail: "loop", type: "keyword" }),
  snippetCompletion("func ${name}(${args}):\n    ${}\nend", { label: "func", detail: "function", type: "keyword" }),
  snippetCompletion("try:\n    ${}\ncatch e:\n    print e.message\nend", { label: "try", detail: "exceptions", type: "keyword" }),
  snippetCompletion("on ${page_loaded}(info):\n    ${}\nend", { label: "on", detail: "event handler", type: "keyword" }),
  snippetCompletion('test "${name}":\n    assert ${cond}\nend', { label: "test", detail: "test block", type: "keyword" }),
  snippetCompletion('service load "${https://example.com}"', { label: "service load", type: "function" }),
  snippetCompletion('service click "${text}"', { label: "service click", type: "function" }),
  snippetCompletion('service type "${text}"', { label: "service type", type: "function" }),
  snippetCompletion('service wait "${text}"', { label: "service wait", type: "function" }),
  snippetCompletion('service search "${text}"', { label: "service search", type: "function" }),
  snippetCompletion("service screenshot", { label: "service screenshot", type: "function" }),
  snippetCompletion("service scroll down ${500}", { label: "service scroll", type: "function" }),
  snippetCompletion("service press ${Enter}", { label: "service press", type: "function" }),
];

function complete(ctx: CompletionContext) {
  const svc = ctx.matchBefore(/service\s+\w*/);
  if (svc) {
    const from = svc.from + svc.text.lastIndexOf(" ") + 1;
    return { from, options: SERVICE_VERBS.map((v) => ({ label: v, type: "function", detail: "service" })) };
  }
  const word = ctx.matchBefore(/[\w.]+/);
  if (!word || (word.from === word.to && !ctx.explicit)) return null;
  return {
    from: word.from,
    options: [
      ...SNIPPETS,
      ...KEYWORDS.map((k) => ({ label: k, type: "keyword" })),
      ...BUILTINS.map((b) => ({ label: b, type: "variable", detail: "builtin" })),
      ...["file.read", "file.write", "file.exists", "file.list", "json.parse", "json.stringify", "ai.ask", "math.random",
        "service.exists", "service.text", "service.url", "service.title"].map((l) => ({ label: l, type: "method" })),
    ],
  };
}

export const mysyaCompletion = autocompletion({ override: [complete] });

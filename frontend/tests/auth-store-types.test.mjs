import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixturePath = path.join(frontend, "tests", "auth-store-contract.fixture.ts");
const configPath = path.join(frontend, "tsconfig.json");
const config = ts.readConfigFile(configPath, ts.sys.readFile);
assert.equal(config.error, undefined);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, frontend,
  { noEmit: true, incremental: false }, configPath);
const format = (diagnostics) => ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCurrentDirectory: () => frontend, getCanonicalFileName: (name) => name, getNewLine: () => "\n",
});
assert.equal(parsed.errors.length, 0, format(parsed.errors));

const contract = `
import { useAuthStore } from "@/stores/authStore";
import type { AuthUser } from "@/types/auth";

type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Expect<T extends true> = T;
type IsAny<T> = 0 extends (1 & T) ? true : false;
type StoreState = ReturnType<typeof useAuthStore.getState>;
type StoreIsTyped = Expect<Equal<IsAny<typeof useAuthStore>, false>>;
type StateIsTyped = Expect<Equal<IsAny<StoreState>, false>>;
type UserIsTyped = Expect<Equal<IsAny<StoreState["user"]>, false>>;
type LoginIsTyped = Expect<Equal<IsAny<StoreState["login"]>, false>>;
type LogoutIsTyped = Expect<Equal<IsAny<StoreState["logout"]>, false>>;
type LoginArguments = Expect<Equal<Parameters<StoreState["login"]>, [
  data: { loginId: string; password: string }, beforeActivate?: () => void,
]>>;
type LogoutArguments = Expect<Equal<Parameters<StoreState["logout"]>, [
  allSessions?: boolean, expectedSessionVersion?: number,
]>>;

const state = useAuthStore.getState();
type User = Expect<Equal<typeof state.user, AuthUser | null>>;
type Initialized = Expect<Equal<typeof state.initialized, boolean>>;
type ErrorMessage = Expect<Equal<typeof state.error, string | null>>;
const selected = useAuthStore((current) => {
  type CurrentIsTyped = Expect<Equal<IsAny<typeof current>, false>>;
  type CurrentMatches = Expect<Equal<typeof current, StoreState>>;
  return current.user;
});
type SelectionIsTyped = Expect<Equal<IsAny<typeof selected>, false>>;
type Selection = Expect<Equal<typeof selected, AuthUser | null>>;
const unsubscribe = useAuthStore.subscribe((current, previous) => {
  type CurrentIsTyped = Expect<Equal<IsAny<typeof current>, false>>;
  type PreviousIsTyped = Expect<Equal<IsAny<typeof previous>, false>>;
  type CurrentMatches = Expect<Equal<typeof current, StoreState>>;
  type PreviousMatches = Expect<Equal<typeof previous, StoreState>>;
});
type Unsubscribe = Expect<Equal<typeof unsubscribe, () => void>>;
const login = state.login({ loginId: "alice", password: "example" }, () => {});
const googleLogin = state.loginWithGoogle("credential", true, () => {});
const logout = state.logout(true, 3);
type LoginResult = Expect<Equal<typeof login, Promise<void>>>;
type GoogleLoginResult = Expect<Equal<typeof googleLogin, Promise<void>>>;
type LogoutResult = Expect<Equal<typeof logout, Promise<boolean>>>;
void state.login({ loginId: "alice", password: "example" });
void state.logout();
`;

function check(source) {
  // Only the fixture is virtual; imports and declarations use the real project.
  // No emit or incremental cache can touch a concurrently running Next build.
  const host = ts.createCompilerHost(parsed.options);
  const readFile = host.readFile.bind(host);
  const fileExists = host.fileExists.bind(host);
  const getSourceFile = host.getSourceFile.bind(host);
  host.fileExists = (name) => name === fixturePath || fileExists(name);
  host.readFile = (name) => name === fixturePath ? source : readFile(name);
  host.getSourceFile = (name, languageVersion, onError, fresh) => name === fixturePath
    ? ts.createSourceFile(name, source, languageVersion, true)
    : getSourceFile(name, languageVersion, onError, fresh);
  const program = ts.createProgram([fixturePath], parsed.options, host);
  assert.ok(program.getSourceFile(path.join(frontend, "src/stores/authStore.ts")),
    "The test must import the actual auth store");
  return ts.getPreEmitDiagnostics(program);
}

test("the actual auth store preserves selector, state, subscription and action types", () => {
  const diagnostics = check(contract);
  assert.equal(diagnostics.length, 0, format(diagnostics));
});

test("the actual auth store rejects invalid fields and action arguments", () => {
  const invalid = [
    ["useAuthStore(current => current.missingField);", 2339],
    ["useAuthStore.getState().missingField;", 2339],
    ["useAuthStore.subscribe((current, previous) => { current.missingField; });", 2339],
    ["useAuthStore.subscribe((current, previous) => { previous.missingField; });", 2339],
    ['state.login({ loginId: 123, password: "example" });', 2322],
    ['state.login("alice");', 2345],
    ['state.logout("all");', 2345],
    ['state.logout(false, "previous-session");', 2345],
    ["state.loginWithGoogle(123);", 2345],
  ];
  const diagnostics = check(contract + invalid.map(([source]) => source).join("\n"));
  const firstInvalidLine = contract.split("\n").length;
  const actual = diagnostics.map((diagnostic) => ({
    file: diagnostic.file?.fileName,
    line: diagnostic.file?.getLineAndCharacterOfPosition(diagnostic.start).line + 1,
    code: diagnostic.code,
  }));
  assert.deepEqual(actual, invalid.map(([, code], index) => ({
    file: fixturePath, line: firstInvalidLine + index, code,
  })), format(diagnostics));
});

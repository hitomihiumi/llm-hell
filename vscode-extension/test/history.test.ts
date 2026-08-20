import assert from "node:assert/strict";
import { test } from "node:test";
import { historyFor, MAX_HISTORY_TURNS } from "../src/format.ts";

/**
 * Handing the conversation to the query planner.
 *
 * This is the reason the chat panel is worth having over the sidebar. The
 * backend plans a search from the transcript: "чи присутній тут гіроскоп"
 * finds nothing on its own — `тут` lives in the previous turn and the
 * datasheet says `IMU: MPU6000`, not "гіроскоп" — and resolves to `F722 IMU`
 * only when the turn that named the board comes with it.
 *
 * Getting this wrong is invisible: the search still runs, still returns
 * something, and is quietly worse.
 */

const asked = (prompt: string) => ({ prompt });
const answered = (text: string) => ({ response: [{ value: { value: text } }] });

test("the conversation reaches the planner in order", () => {
  const history = historyFor([asked("що це за плата F722"), answered("Matek F722-HD")]);

  assert.deepEqual(history, [
    { role: "user", content: "що це за плата F722" },
    { role: "assistant", content: "Matek F722-HD" },
  ]);
});

test("only the prose of a response is carried", () => {
  /* A response also holds references and anchors. An anchor has a `value`
     too, and it is a Uri - so "has a value" is not the test. */
  const history = historyFor([
    {
      response: [
        { value: { value: "The gyro is an MPU6000" } },
        { value: { scheme: "https", path: "/x" } },
        { command: { command: "kb.signIn", title: "Sign in" } },
      ],
    },
  ]);

  assert.deepEqual(history, [{ role: "assistant", content: "The gyro is an MPU6000" }]);
});

test("a turn that said nothing is not a turn", () => {
  assert.deepEqual(historyFor([asked("   "), answered(""), { response: [] }]), []);
});

test("an empty transcript is an empty history, not a failure", () => {
  assert.deepEqual(historyFor([]), []);
});

test("only the recent turns are carried", () => {
  const long = Array.from({ length: 40 }, (_, index) => asked(`question ${index}`));

  const history = historyFor(long);

  assert.equal(history.length, MAX_HISTORY_TURNS);
  assert.equal(history.at(-1)?.content, "question 39");
});

test("a turn longer than the API accepts is cut rather than rejected", () => {
  const history = historyFor([asked("x".repeat(9000))]);

  assert.equal(history[0].content.length, 8000);
});

test("the parts of one response are joined, because streaming splits them", () => {
  /* The participant streams an answer a token at a time, and each call to
     `stream.markdown` becomes its own part. Reading only the first would
     hand the planner one word of the previous answer. */
  const history = historyFor([
    {
      response: [
        { value: { value: "The " } },
        { value: { value: "team " } },
        { value: { value: "object" } },
      ],
    },
  ]);

  assert.equal(history[0].content, "The team object");
});

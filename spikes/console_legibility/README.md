# Console legibility cold read

`evidence.json` records a zero-context read of the deployed console home: a
fresh reader with no repository access was given only the URL and asked what
the fleet and a payload are and how they relate. The verdict is PASS only if
the answer names both correctly; a partial answer is a FAIL.

Redo it after any change to the console's context strip or orientation
figure: dispatch a fresh reader with the prompt in the file, paste the
verbatim answer, grade it against `grading_rule`, and record the console
revision it read.

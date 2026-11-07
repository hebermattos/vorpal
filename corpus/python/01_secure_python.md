# Secure Python

Use SQL parameterization instead of interpolating user input into the query.
Example: cursor.execute("SELECT name FROM users WHERE id = ?", (user_id,))
Validate the types and sizes of external inputs before processing them.
Do not store passwords in clear text; use a modern password hashing library.
Avoid shell commands with shell=True and untrusted data.
Include automated tests for invalid inputs and limits.
The absence of test failures does not prove the absence of vulnerabilities.
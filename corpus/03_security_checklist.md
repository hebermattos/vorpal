# Defensive application review

1. Identify exposed components and untrusted inputs.
2. Apply the principle of least privilege to processes and credentials.
3. Check dependency updates and security fixes.
4. Do not log credentials, tokens, cookies, or other sensitive data.
5. Validate inputs and parameterize commands and queries.
6. Write unit and integration tests for negative cases.
7. Restrict file access to the allowed directory.
8. Review TLS settings and relevant HTTP headers.
9. Perform static analysis with trusted tools and manually review alerts.
10. Run security tests only in authorized environments.
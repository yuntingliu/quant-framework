Hosted release contract:
1. Supabase stores tenant, package, version, run, audit, and artifact metadata with RLS.
2. Vercel serves one multi-tenant web shell and resolves /h/{slug} or {slug}.apps.example.com.
3. Railway runs the shared control plane and hosted Harness runtime API.
4. A release publishes package data; it does not provision three new cloud projects.
5. Provider credentials never leave the control plane.
6. Desktop-only capabilities must declare a hosted adapter or block release.
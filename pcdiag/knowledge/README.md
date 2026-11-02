# Guided troubleshooter knowledge base (started in v0.4.0)

Plain data files for devices pcdiag can't scan directly: phones, tablets,
game consoles and accessories. Each file lists symptoms, and each symptom
lists checks in order: free and safe first, then steps that need a tool,
and a clear "stop and see a technician" line. The same files will power the
web troubleshooter and the Android app later, so they stay as JSON with no
code.

Fields per symptom: `id`, `title`, `steps` (each with `do`, `why`, and
`needs`: `nothing`, `tool` or `part`), and `stop_if` (signs that mean the
user should stop and get professional help). Safety rules in every file
apply to all of its symptoms.

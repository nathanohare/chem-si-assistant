# To do later

Open tasks that need something we don't have yet. When one is done, delete it here and
record the result in SPEC.md.

## Check the clipboard on a Mac (needs a Mac)

**Why:** on macOS the program probably cannot see ChemDraw's structure data or the vector
(PDF) copies from ChemDraw and Mnova. Pasted structures may then not paste at all, and
pictures or spectra may paste only as low-resolution images, with no warning. Nobody has
been able to check this yet (SPEC §13r.3; audit finding cross-platform-4).

**What to do on the Mac** (ChemDraw and Mnova installed):

1. In ChemDraw, draw a small structure, select it and press ⌘C.
2. Open the **Terminal** app and run:

       osascript -e 'clipboard info'

3. Copy everything it prints.
4. In Mnova, open any spectrum, select it and press ⌘C (Edit ▸ Copy).
5. Run the same command in Terminal again and copy what it prints.
6. Send both outputs to Claude (or paste them into a new GitHub issue).

**What happens next:** the outputs show which clipboard formats macOS really offers. If the
PDF and ChemDraw formats are there, a small Mac-only clipboard reader is added (no new
dependency). It is used only when Qt's normal check finds nothing, and a one-line note is
shown when a paste falls back to a picture.

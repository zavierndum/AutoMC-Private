---
name: Files, documents, web and Python
description: Which general tool to use when (files, knowledge bases of PDFs and images, the web, Python, the workspace's memory and earlier conversations), what each asks the user first, and what each refuses.
---

# general-tools

## Which tool
- A file the user names (CSV, Excel, text, a PDF's text, an HDF5 file's structure): FileReadTool, after FileListTool if you do not know
  the name. A note or a table the user wants kept: FileWriteTool.
- Reports, papers, drawings, figures: a knowledge base. DocumentParseTool on the folder that holds them, then DocumentSearchTool.
- Public, current or general facts that are not in the user's documents: WebSearchTool, then WebFetchTool for a page or a PDF.
- Analysis, plots and calculations on data the tools can reach: PythonRunTool.
- A simulation: never one of these. Build with the TRIGA tools and start with OpenMCRunStartTool.

## Files
- Paths are relative to the workspace, or absolute; a Windows path (C:\...) works. The session block names the workspace and the
  folders you may read; a path elsewhere is refused.
- A big file comes back in parts: ask for the next part (start, sheet, pages) rather than the whole file again.
- Write only in the workspace, never inside a case, a run or AutoMC's own folders, and overwrite only when the user asked for it.
- Keys, .env files and credentials are always refused. Do not try another path to them, and never ask the user for a key.

## Documents
- A knowledge base is a folder of PDFs and images. For one file, give the folder that holds it. DocumentParseTool returns a plan and the
  chat asks the user; the run goes on in the background (minutes). Running it again later parses only the files added since.
- To answer from documents, call DocumentSearchTool with the user's question in plain words and answer only from the rows it returns,
  citing the file and the page. A visual row is a vision model's description of a figure, a table or an image: say so when a number
  comes from one.
- A value the user asks for may have to be derived from the rows (the difference of two labelled radii): compute it and show the
  numbers used. When the rows do not hold the answer, say that; do not fill in from memory.
- DocumentCheckTool shows what a knowledge base holds and how a run is going.

## Web
- The query and the URL leave this machine. Write only what the conversation may show the web, never a facility value.
- A conversation holding restricted data cannot use the web, with any model; one holding data of unknown origin asks the user to type
  'yes, send'. If the tool refuses, say why and suggest a new conversation for the web question.
- Cite every URL you use. WebFetchTool reads one page or PDF in parts; a PDF it saves can become a knowledge base (DocumentParseTool on
  the folder its result names).

## Python
- To run code, call PythonRunTool with the code. The chat shows the user the code and asks them; never ask for approval in your
  text, never invent a phrase for the user to type, and never say you cannot run code.
- A run cannot read typed input: never use input(). Put the values in the code; if the user has not given one, ask in the chat
  first, then run with it.
- The user sees the code and says yes to every run, so keep each run short and focused on one step.
- Variables, imports and files stay in the conversation's kernel between runs: reuse them instead of recomputing.
- Never start transport with it (openmc.run, the openmc executable): a run started that way is not approved, tracked or labelled.
- A long output is shortened to its start and end; the whole of it is in the file the result names (read more with FileReadTool).
  Quote numbers exactly as printed. A figure is saved as a PNG in the kernel's folder: tell the user where.
- If the user declines a run, nothing ran: do not report a result from it.
- If the result says the output was withheld from you, the user has already seen it in the chat: say so, do not guess it, and do
  not suggest another model or offer to read its file (the file is withheld from you too). They can type 'yes, send' at the next
  run's question so that you can read its output, or tell you the value.

## Memory and earlier conversations
- The prompt's "This workspace" part holds the user's AUTOMC.md and the index of the workspace's notes. Follow AUTOMC.md; read a
  note whole with MemoryReadTool when it bears on the question.
- Propose a note with MemoryWriteTool only for a lasting fact or preference: the user asked you to remember it, or made a decision
  later conversations need. One fact per note, never what the tools can find again. The user approves each note; you never set its
  label.
- When the user refers to an earlier conversation ("as we found last week"), use ChatSearchTool, and say which conversation and when.
  What was said then was not checked since: verify numbers with the tools before relying on them.

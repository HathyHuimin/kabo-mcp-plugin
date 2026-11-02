# Approved script: a talking video of the creator's own words

Read this when the user already has the exact words they want to say: pasted here, or a script
written earlier in this conversation and approved (for example by `create-script-and-packaging`).
There is no reference video. Kabo makes a talking video of the creator saying those words, using
the creator's own footage for face and voice. This is the same remake job as the reference path;
only steps 1-4 differ, and the rest of the Skill (status polling, the model choice, production,
delivery, the final review, failures) applies unchanged.

> Host edition (ported from creator-remake-web 0.1.32, creator-skills `f74df60a`, approved-script
> workflow). Use it only when the user asks for a video of a script; a request to remake a video
> follows the reference path.

## 1. Inputs

- **The approved spoken text**, exactly as the user approved it. If more than one script could be
  meant, ask which one. Do not ask for a reference, portrait, topic, rewrite or language: the
  text sets the output language; never translate it.
- **The creator's own footage**, as on the reference path: their Instagram handle or profile link,
  or one to four YouTube, Instagram or TikTok links to videos where they talk to camera. A script
  may have come from someone else's video; that never makes that video the creator's footage.

Save the text to a file in the run directory without changing a character (for example
`script/approved.txt`), then build the call:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/static_host.py' start-call --script-file 'script/approved.txt' --instagram-handle '<handle or profile link>'
```

Use `--creator-video` (and `--creator-name`) instead of `--instagram-handle` exactly as on the
reference path; `--language` does not apply. Call `start_remake_video` with the printed arguments
and handle its results and errors as in step 2 of the Skill. A `service_unavailable` error whose
reason is `script_video_unavailable` means this input is not available yet: say nothing was
started or charged, and stop. Never fall back to a reference-video remake.

## 2. At waiting_script: copy the drafts, do not write

Fetch the brief as in step 3. `brief/work/brief_input.json` has `input_kind` `script`; its
`approved_script` is the source of truth. Do not write the four documents yourself. Copy the
runner's drafts unchanged:

```sh
cd '<run directory>' && KABO_STATIC_RUN_DIR='<run directory>' python3 '<skill directory>/scripts/check_documents.py' copy-drafts
```

It writes `script/*.json` from the drafts and checks them. Only where the lines break (and each
line's performance direction) may change in `script/script.json`. Never proofread, rewrite an
opening or call to action, add claims, translate or drop a word. The runner checks the words
again before anything is generated. The empty image-jobs document is expected: there is no
reference scene.

## 3. Model, production and delivery

Go to step 6 of the Skill: show the model options and wait for the user's choice, exactly as for
a reference remake; never preselect. At delivery, say the approved script was used word for word
and only the line breaks may differ; do not say you rewrote anything. If the runner rejects the
text check, restore the exact approved text with `copy-drafts`; never change the words to pass.
A change to the words is a new approved script and needs a new job once the user approves it.

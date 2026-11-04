# Creator photos instead of videos

Read this when the user has photos of the creator but few or no videos of them talking. Kabo
fetches each photo from its link and uses it for the creator's appearance; the voice comes from a
video of them speaking or, only when there is none, from a stock voice.

- **Photos**: one to four public links to JPEG or PNG images of the creator's face (a direct image
  link that opens without signing in). Pass each with `--creator-photo '<link>'`. Videos and photos
  together are at most four links (`--creator-video` and `--creator-photo`), and `--creator-name`
  is required.
- **Voice**: with at least one `--creator-video` of the creator speaking, the voice is cloned from
  it. With photos only, first ask the user for a public link to a video of the creator speaking
  (10-30 seconds is enough). Only if they have none, offer the four stock voices by number and name
  and wait for their pick, then add `--voice-preset '<id>'`:
  1. Female soft: `933563129e564b19a115bedd57b7406a`
  2. Female bright: `b545c585f631496c914815291da4e893`
  3. Male calm: `536d3a5e000945adb7038665781a4aca`
  4. Male energetic: `802e3bc2b27e49c2995d23ef70e6ac89`
  Never pick a stock voice for the user, and never use one when a video of them speaking exists.
- `start_remake_video` may refuse with `validation_error` before anything is charged:
  `voice_sample_needed` (photos only, no voice: ask as above), `photo_url_unreadable` or
  `photo_url_invalid` (a link is not a reachable public JPEG/PNG: ask for another link),
  `creator_appearance_missing`. Relay the message in plain words.
- At delivery, if the notes say the appearance came from photos or a stock voice was used, say so
  in one line: likeness from photos is weaker than from video, and a stock voice is not their own.

# When a remake tool returns an error

## start_remake_video

A tool error carries `code`, `message` and `retryable`. No remake was started by any of them;
relay `message` in plain words:

- `validation_error` (a link or handle that cannot be used, a reference over 10 minutes, a video
  that cannot be fetched, no usable reels, the same video in both roles): no remake fee was charged
  and the daily remake limit was not used. Ask for a different input instead of resubmitting.
- `quota_exceeded` with `retryable: false`: the daily remake limit (or the daily data allowance)
  is reached. Say no new remake was started and another can start tomorrow.
- `quota_exceeded` with `retryable: true`: another remake is still running. Say one remake runs
  at a time and offer to start this one when it finishes.
- `conflict`: this key was already used with different inputs. Build the arguments again with
  `--attempt 2` (then 3, ...) and call again.
- `subscription_required`: a remake needs a Kabo Pro subscription; say so.
- `service_unavailable` with `retryable: false`: Kabo could not fetch the videos on its side; no
  remake was started. Do not retry with the same key. Say so; if the user wants to try again later,
  build the arguments again with the next `--attempt` and call once.
- `service_unavailable` or another error with `retryable: true`: try once more after a minute; if
  it fails again, say remakes are unavailable right now.

## render_remake_video

- `conflict` with `retryable: true`: the job's brief could not be read. Call once more after a
  minute, then report it.
- `quota_exceeded` with `retryable: true`: another remake of this account is still being prepared
  or rendered; one runs at a time. Nothing was started and the job keeps waiting with its script:
  tell the user, wait for the other one (poll this job with `get_remake_video` every few minutes, or
  let the user ask later) and call `render_remake_video` again with the same arguments.

## start_video_project

Read its errors like those of `start_remake_video` above. Starting a project generates nothing and
charges nothing; fetching the creator's videos uses the daily data allowance.

# ugc-pipeline
UGC from request to finished

Goal: This is a multi-tenant pipeline of LLMs and scripts that can take any description from a client to create a short UGC video and turn it into a high quality video output. Human operator in the loop.

Process A
- receive multimodal input: image, video, text from outside world (= "Client Request")
- copy input into container/folder of client, clearly marked as input.
- As an example: client_Brutus/request_5/inputs (all inputs in this folder)
- Run pre-check to discard illegal contents.
- Trigger process in container (B)

Process B
- This is a scoped process it can only act within the container of this one client, it can never "escape"
- It has access / context to all the requestes from the same client
- First step is categorizing the request into one of four options: `still_images`, `avatar_voice`, `voice_over`, or `motion_control` for video-driven motion transfer.
- Then script writing -> script.md
- Then img prompts, video, audit prompts (in logical order, usually images needed for video)
- as an example client_Brutus/request_5/prompts.json (a list in order defining prompts, inputs, models for each)
- Make concrete using fal.ai Nano Banana for static images only, with no more than four generated images per final clip.
- Always turn the static images into a silent 9:16 MP4 with local ffmpeg effects such as zooms, pans, rapid visual beats, text overlays, color polish, and a CTA. Audio is ignored for now.
- Self-document -> learning.md describe what steps were taken, whether they worked as expected and what should be improved.

Process C
- Scoped process in client container
- Evaluate output of Process B in isolation + in context of other work for same client + how reasonable learning.md is (might add something)
- Assign score: Likelihood of success
- Escalate to human -> Some UI needed that hides intermediate steps just shows all inputs and the final result.
- human can write note -> human.md

Process D 
- "Sleeping" = thinking hard about new infromation and incorporating it into "brain"
- This process should read the learning.md and the human.md in a certain timeframe. And the overall Brain.md (which must document the details of all processes) and then modify the brain.md if necessary.
- Must always scrap the PII information during the sleep process -> brain.md should not contain concrete client names or exact project references

# Feature Specification: Recorded Video Camera

**Feature Branch**: `001-recorded-video-camera`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "need a python tool that will first record a video of me, store it and then allow it to be used in teams/zoom meetings instead of my actual camera"

## Clarifications

### Session 2026-11-11

- Q: How do you want to operate the tool: by typing commands, through a small window with
  buttons, or from an icon in the system tray? → A: Command line now, designed so a window or
  tray icon can be added in a later feature.
- Q: When the recording reaches its end and starts again, how should the tool hide the jump
  between the last frame and the first? → A: Blend the end of the recording smoothly into its
  start.
- Q: When the tool is running but not presenting a recording (before start, after stop, or after
  a failure mid-meeting), what should a meeting that has the tool's camera selected show? → A: A
  neutral placeholder picture.
- Q: Should you be able to add a video file you already have to the tool's library, as well as
  recording with the tool? → A: Yes; an import command adds an existing video file after checking
  it is usable, and rejects unusable files with the reason.
- Q: What picture quality should the tool record and present? → A: Record and keep at the
  camera's or file's own quality (up to 1080p); present at 720p by default, with 1080p as a
  setting; 30 pictures per second.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Present a stored recording as my camera in a meeting (Priority: P1)

As a meeting participant, I start the tool, pick one of my stored recordings, and the tool makes
it available as a camera that I can select in Teams or Zoom. Other participants see the recording
in my video tile instead of my live camera. The recording repeats for as long as I keep it running.

**Why this priority**: This is the outcome the tool exists for. Without it, recording and storing
video has no purpose. It can be proven with any existing video file of the user before the
recording story is built.

**Independent Test**: Import a suitable video of the user into the tool's library, start
presentation, select the tool's camera in Teams and in Zoom, and confirm a second participant sees
the recording continuously for a full meeting.

**Acceptance Scenarios**:

1. **Given** a stored recording and no presentation running, **When** the user starts presenting
   that recording, **Then** a camera provided by the tool appears in the camera list of Teams and
   Zoom and shows the recording when selected.
2. **Given** the recording is being presented in a meeting, **When** playback reaches the end of
   the recording, **Then** the end blends smoothly into the start without the video tile going
   blank, freezing or jumping abruptly.
3. **Given** the recording is being presented, **When** the user stops presentation, **Then**
   within 2 seconds the tool's camera shows a neutral placeholder picture instead of the
   recording, and the user can select their real camera in the meeting application.
4. **Given** the recording is being presented, **When** the user asks the tool for its status,
   **Then** the tool states that it is presenting, which recording, for how long, and whether the
   "recording" indicator is on.
5. **Given** the user has not changed the indicator setting, **When** the recording is presented,
   **Then** no indicator is shown; **When** the user switches the indicator on, **Then** other
   participants see a "recording" indicator on the video for the rest of the presentation.

---

### User Story 2 - Record a video of myself (Priority: P2)

As a user, I record a video of myself from my real camera using the tool, see a preview while
recording, and stop when I am happy. The tool keeps the recording so I can use it later.

**Why this priority**: The user wants to produce the recording with the same tool rather than
supply a file made elsewhere. It depends on nothing else and feeds Story 1.

**Independent Test**: Start a recording, record for 30 seconds, stop, and confirm the recording
plays back correctly from the tool's library.

**Acceptance Scenarios**:

1. **Given** a working camera, **When** the user starts a recording, **Then** the tool shows a
   live preview and a clear indication that recording is in progress.
2. **Given** a recording in progress, **When** the user stops it, **Then** the recording is saved
   under a name the user gave (or a default name containing the date and time) and appears in the
   library.
3. **Given** a recording in progress, **When** the user cancels it, **Then** nothing is saved.
4. **Given** more than one camera is connected, **When** the user starts a recording, **Then** the
   user can choose which camera to record from.

---

### User Story 3 - Manage stored recordings (Priority: P3)

As a user, I list my stored recordings, preview one, rename it, choose which one is the default
for presentation, and delete those I no longer want.

**Why this priority**: Useful once several recordings exist (different clothing, lighting,
rooms), but a single recording is enough for the tool to deliver value.

**Independent Test**: With three stored recordings, list them, preview one, rename one, set a
default, delete one, and confirm the library reflects each change.

**Acceptance Scenarios**:

1. **Given** stored recordings, **When** the user lists them, **Then** each is shown with its
   name, duration, date recorded and size.
2. **Given** a stored recording, **When** the user deletes it and confirms, **Then** it is removed
   from the library and from storage.
3. **Given** a default recording is set, **When** the user starts presentation without naming a
   recording, **Then** the default is presented.

---

### Edge Cases

- The real camera is in use by another application when the user starts a recording: the tool
  reports this and does not create an empty or corrupt recording.
- Storage runs out during a recording: the tool stops, tells the user, and keeps whatever was
  recorded up to that point as a valid recording or discards it cleanly.
- The user starts presentation while a presentation is already running: the tool reports the
  running presentation instead of starting a second one.
- The stored recording is missing, moved or unreadable when presentation starts: the tool reports
  which recording failed and presents nothing.
- Presentation fails during a meeting: the tool's camera shows the neutral placeholder picture; it
  never silently switches to the live camera and never shows a frozen frame of the recording.
- The tool itself is closed or crashes during a meeting, so it can no longer supply any picture:
  the meeting shows no video from the tool's camera, and the live camera is still not shown.
- A recording is too short to blend (under 5 seconds): the tool warns the user at save time but
  still allows it, and repeats it without a blend.
- The first and last frames of a recording differ noticeably, so the blend at the repeat point
  would show as ghosting: the tool warns the user when the recording is saved.
- The meeting application was already running before presentation started and does not list the
  tool's camera: the tool's guidance tells the user how to make it appear.
- The user imports a file that is not a video, is damaged, or cannot be read: the tool rejects it,
  names the reason, and adds nothing to the library.
- The user imports a file with the same name as an existing recording: the tool asks for a
  different name rather than replacing the existing recording.
- The user tries to delete the recording that is currently being presented: the tool refuses until
  presentation stops.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to record video of themselves from a camera connected to their
  computer, with a live preview while recording.
- **FR-002**: Users MUST be able to choose which camera to record from when more than one is
  available.
- **FR-003**: Users MUST be able to start, stop and cancel a recording; a cancelled recording
  leaves nothing stored.
- **FR-004**: The tool MUST store each completed recording on the user's own computer, with a
  name, duration, date recorded and size, and keep it available across restarts.
- **FR-005**: Users MUST be able to list, preview, rename and delete stored recordings, and mark
  one as the default.
- **FR-006**: The tool MUST present a chosen stored recording as a camera that Microsoft Teams and
  Zoom list alongside the user's real cameras.
- **FR-007**: While presenting, the tool MUST repeat the recording continuously with no blank or
  frozen picture at the point where it repeats, and MUST blend the final second of the recording
  smoothly into its start so that there is no abrupt jump.
- **FR-008**: Users MUST be able to stop presentation at any time, and the recording MUST stop
  being shown within 2 seconds.
- **FR-020**: Whenever the tool's camera is selected in a meeting application and the tool is
  running but not presenting (before presentation starts, after it is stopped, or after it
  fails), the camera MUST show a neutral placeholder picture that contains no image of the user.
- **FR-009**: The tool MUST show, on request and while presenting, whether it is presenting, which
  recording, and for how long.
- **FR-010**: The tool MUST allow only one presentation at a time.
- **FR-011**: The tool MUST NOT show the live camera to a meeting through its own camera at any
  time; live camera access is used only during recording and preview.
- **FR-012**: Recordings MUST NOT leave the user's computer; the tool sends no recording or part
  of one to any other service.
- **FR-013**: The tool MUST refuse to delete a recording that is currently being presented.
- **FR-014**: The tool MUST report failures (camera unavailable, storage full, recording missing
  or unreadable, presentation could not start) in plain language naming the cause and what the
  user can do next.
- **FR-015**: The tool MUST warn the user when a saved recording is too short to blend (under 5
  seconds), or its start and end differ enough that the blend would show as ghosting.
- **FR-016**: The tool MUST keep a record of when recordings were made, imported, presented and deleted,
  without including any picture content.
- **FR-017**: The tool MUST offer a visible "recording" indicator on the presented video that the
  user can switch on or off. It is off by default; when on, other participants see the indicator
  for the whole presentation. The status in FR-009 MUST state whether the indicator is on.
- **FR-022**: The tool MUST record at the chosen camera's own picture quality, up to 1080p at 30
  pictures per second, and MUST keep recorded and imported video at its own quality up to that
  limit; higher-quality imports are reduced to 1080p.
- **FR-023**: The tool MUST present at 720p and 30 pictures per second by default, and MUST let
  the user switch presentation to 1080p. A recording of lower quality than the presentation
  setting is presented at its own quality and is not enlarged.
- **FR-021**: Users MUST be able to import an existing video file into the library. The tool MUST
  check that the file is a readable video before adding it, MUST reject an unusable file with the
  reason and leave the library unchanged, and MUST leave the original file untouched. An imported
  recording behaves like one made with the tool, including the warnings in FR-015.
- **FR-018**: Users MUST be able to perform every operation (record, import, list, preview, rename,
  delete, set default, present, stop, status, switch the indicator) by typing a command. A
  picture window opens only while recording or previewing.
- **FR-019**: Every operation MUST be usable without the command interface, so that a window or
  system tray front end can be added in a later feature without changing how recording, storage
  or presentation behave.

### Key Entities *(include if feature involves data)*

- **Recording**: A stored video of the user. Has a name, duration, date recorded, size, picture
  dimensions, whether it is the default, and its origin (recorded with the tool or imported).
  Created by a recording session or an import; used by presentation sessions.
- **Recording Session**: One attempt to capture video from a real camera. Has the camera used,
  start time, and outcome (saved, cancelled, failed).
- **Presentation Session**: One period during which a recording is offered as a camera. Has the
  recording presented, start time, end time, and how it ended (stopped by user, failed).
- **Activity Record**: An entry noting that a recording was made, presented or deleted, with the
  time and outcome. Contains no picture content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with no prior setup beyond installing the tool can record a video and have it
  showing in a Teams or Zoom meeting within 5 minutes.
- **SC-002**: A user with a stored recording can go from starting the tool to the recording being
  visible in a meeting in under 30 seconds.
- **SC-003**: A recording is presented continuously for a 2-hour meeting with no blank, frozen or
  visibly stuttering picture as seen by another participant.
- **SC-004**: The tool's camera delivers the picture to the meeting application at the selected
  presentation quality (720p by default, 1080p when selected) at a steady 30 pictures per second,
  with no more than 1% of pictures dropped or repeated over a 2-hour meeting.
- **SC-005**: After the user stops presentation, the recording is no longer visible to other
  participants within 2 seconds, in 100% of attempts.
- **SC-006**: In no test does the live camera picture reach a meeting through the tool's camera.
- **SC-007**: The user's computer remains responsive enough to take part in the meeting (speak,
  share screen, take notes) while a recording is being presented.

## Assumptions

- The tool is for a single person using their own computer and their own likeness. Presenting a
  recording of someone else is out of scope.
- Video only. The user's microphone and meeting audio are untouched; the recording's sound, if
  any, is not played into the meeting.
- The recording is replayed as recorded. Generating new video (lip-sync to live speech, reacting
  to the meeting, AI-generated likeness) is out of scope.
- The user chooses the tool's camera inside Teams or Zoom themselves; the tool does not join
  meetings, control the meeting application, or sign in to any meeting service.
- The user's computer runs Windows, where the project is being developed. Other operating systems
  are out of scope for this feature.
- Teams and Zoom desktop applications are the targets. Browser-based meetings and other meeting
  applications may work but are not verified.
- Installing a component that lets other applications see the tool's camera is acceptable and may
  need administrator rights once.
- The user is responsible for complying with their organisation's and meeting hosts' rules on
  camera use; the tool states this at first use.
- The "recording" indicator is off by default because the tool is being built for testing
  purposes. Constitution v2.0.0 (Principle I) permits this: the indicator must be available, and
  the user is responsible for disclosure when it is off.
- A desktop window or system tray front end is out of scope for this feature; only the command
  interface is delivered.
- Recordings are kept until the user deletes them; there is no automatic expiry.
- Recordings of a few minutes at up to 1080p are the expected size; hour-long recordings are not
  a goal.
- The meeting application decides what quality other participants finally receive; the tool is
  responsible only for what it delivers to the meeting application.

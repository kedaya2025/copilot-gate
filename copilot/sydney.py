"""Sydney-protocol driver for Microsoft 365 Copilot (enterprise/work accounts).

Speaks the Sydney/Bing Chat WebSocket protocol used by enterprise Copilot at
``wss://substrate.office.com/m365Copilot/Chathub/...``. This is a completely
different protocol from the consumer Copilot's ``/c/api/chat``:

  * Consumer: ``send`` -> ``appendText``* -> ``done``
  * Enterprise (Sydney): ``{"type":4,"target":"chat"}`` -> ``writeAtCursor``* ->
    ``{"type":2}`` -> ``{"type":3}``

The enterprise endpoint also does **not** use Cloudflare, so there is no
``cf_clearance`` to earn or refresh — a major simplification over the consumer
driver.

The chat token is a JWT with ``aud=https://substrate.office.com/sydney`` and
scope ``sydney.readwrite``, captured off the page's own chat WebSocket during
login (see :mod:`copilot.browser`).
"""

import json
import time
import uuid
from select import select
from typing import Dict, Generator, Optional
from urllib.parse import quote

from curl_cffi.const import CurlECode, CurlInfo
from curl_cffi.curl import CurlError
from curl_cffi.requests import Session, CurlWsFlag

from .models import AbstractProvider, Conversation, ImageResponse, ImageType
from .useragent import CHROME_CLIENT_HINTS, CHROME_UA, IMPERSONATE_TARGET
from .utils import drain_json

_CURL_SOCKET_BAD = -1

# Feature flags passed in the WS URL. Captured from a live enterprise session;
# these are mostly constant and control which features the backend enables.
_VARIANTS = (
    "EnableMcpServerWidgets,feature.EnableMcpServerWidgets,"
    "feature.EnableImageGenInsufficientTokensThrottled,"
    "feature.EnableImageGenSystemCapacityThrottled,"
    "feature.EnableLuForChatCIQ,feature.enableChatCIQPlugin,EnableRequestPlugins,"
    "feature.EnableSensitivityLabels,EnableUnsupportedUrlDetector,"
    "feature.IsCustomEngineCopilotEnabled,feature.bizchatfluxv3,"
    "feature.enablechatpages,feature.enableCodeCanvas,"
    "feature.turnOnWorkTabRecommendation,feature.turnOnDARecommendation,"
    "feature.IsStreamingModeInChatRequestEnabled,IncludeSourceAttributionsConcise,"
    "SkipPublishEmptyMessage,feature.EnableDeduplicatingSourceAttributions,"
    "feature.IsCitationsReferencesOutputEnabled,"
    "feature.enableDeltaStreamingForReferences,"
    "feature.enableIncludeReferencesInDeltaResponse,"
    "feature.enablereferencesforagents,Enable3PActionProgressMessages,"
    "feature.enableClientWebRtc,feature.EnableMeetingRecapOfSeriesMeetingWithCiq,"
    "feature.EnableReferencesListCompleteSignal,feature.StorageMessageSplitDisabled,"
    "feature.EnableCuaTakeControlApi,SingletonEnvOn,cdxenablefccinmainline,"
    "EnableComposeWidget,-agt_researcheragent_enableMemoryRead,"
    "feature.cwcallowedos,feature.EnableMergingPureDeltas,"
    "feature.disabledisallowedmsgs,feature.enableCitationsForSynthesisData,"
    "feature.EnableConversationShareApis,feature.enableGenerateGraphicArtOptionsSet,"
    "cdximagen,feature.EnableUpdatedUXForConfirmationDialog,"
    "feature.EnableContentApiandDocTypeHtmlInRichAnswers,"
    "cdxgrounding_api_v2_rich_web_answers_reference_bottom_force,"
    "cdxenablerenderforisocomp,feature.EnableClientFileURLSupportForOfficeWebPaidCopilot,"
    "feature.EnableDesignEditorImageGrounding,feature.EnableDesignerEditor,"
    "feature.EnableSkipRehydrationForSpeCIdImages,feature.EnablePersonalization,"
    "rich_responses,feature.EnableBase64DataInMessageAnnotations,"
    "feature.EnableSkipEmittingMessageOnFlush,feature.EnableRemoveEmptySourceAttributions,"
    "feature.EnableRemoveStreamingMode,feature.OfficeWebToHelix,"
    "feature.OfficeDesktopToHelix,feature.M365TeamsHubToHelix,"
    "feature.OwaHubToHelix,feature.MonarchHubToHelix,"
    "feature.Win32OutlookHubToHelix,feature.MacOutlookHubToHelix,"
    "Agt_bizchat_enableGpt5ForHelix"
)

# The optionsSets control which processing pipeline the backend uses.
_OPTION_SETS = [
    "search_result_progress_messages_with_search_queries",
    "update_textdoc_response_after_streaming",
    "deepleo_networking_timeout_10minutes_canmore",
    "cwc_flux_image", "cwc_code_interpreter",
    "cwc_code_interpreter_amsfix", "cwcfluxgptv",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "gptvnorm2048", "cwc_code_interpreter_citation_fix",
    "code_interpreter_interactive_charts",
    "cwc_code_interpreter_interactive_charts_inline_image",
    "code_interpreter_matplotlib_patching", "cwc_fileupload_odb",
    "update_memory_plugin", "add_custom_instructions",
    "cwc_flux_v3", "flux_v3_progress_messages", "enable_batch_token_processing",
    "enable_gg_gpt", "flux_v3_references", "flux_v3_references_entities",
    "flux_v3_image_gen_enable_dimensions",
    "flux_v3_image_gen_enable_non_watermarked_storage",
    "flux_v3_image_gen_enable_icon_dimensions",
    "flux_v3_image_gen_enable_system_text_with_params",
    "flux_v3_image_gen_enable_designer_dimensions_meta_prompting_in_system_prompts",
    "flux_v3_image_gen_enable_story", "rich_responses",
]

_ALLOWED_MESSAGE_TYPES = [
    "Chat", "Suggestion", "InternalSearchQuery", "Disengaged",
    "InternalLoaderMessage", "Progress", "GeneratedCode", "RenderCardRequest",
    "AdsQuery", "SemanticSerp", "GenerateContentQuery", "GenerateGraphicArt",
    "SearchQuery", "ConfirmationCard", "AuthError", "DeveloperLogs",
    "TriggerPlugin", "HintInvocation", "MemoryUpdate", "EndOfRequest",
    "TriggerConfirmation", "ResumeInvokeAction", "ResumeUserInputRequest",
    "TriggerUserInputRequest", "EscapeHatch", "TriggerPluginAuth",
    "ResumePluginAuth", "SideBySide", "ReferencesListComplete",
    "SwitchRespondingEndpoint",
]


class SydneyDriver(AbstractProvider):
    """Pure-HTTP driver for Microsoft 365 Copilot using the Sydney protocol.

    Unlike the consumer :class:`copilot.driver.Copilot`, this driver:
      * Connects to ``wss://substrate.office.com/m365Copilot/Chathub/...``
      * Speaks the Sydney/Bing Chat WebSocket protocol (SignalR-like)
      * Does **not** need Cloudflare clearance
      * Does **not** need a separate conversation-creation REST call — the
        server creates the conversation and returns its id in the response.
    """

    label = "Microsoft 365 Copilot (Enterprise)"
    url = "https://m365.cloud.microsoft"
    working = True
    supports_stream = True
    default_model = "Copilot"
    needs_auth = True

    def create_completion(
        self,
        prompt: str,
        stream: bool = False,
        proxy: str = None,
        timeout: int = 900,
        image: ImageType = None,
        conversation: Optional[Conversation] = None,
        conversation_id: str = None,
        return_conversation: bool = False,
        cookies: Dict[str, str] = None,
        access_token: str = None,
        identity_type: str = None,
        ws_url: str = None,
        **kwargs,
    ) -> Generator:
        """Stream a Microsoft 365 Copilot reply to ``prompt``.

        Uses the Sydney WebSocket protocol: handshake -> chat -> update frames
        -> completion. The server assigns the conversation id (returned in the
        ``type:2`` frame); pass it back via ``conversation_id`` to continue.
        """
        # The WS base URL (wss://substrate.office.com/m365Copilot/Chathub/<oid>@<tid>)
        # was captured during login and stored in token.json as ``ws_url``.
        if not ws_url:
            raise RuntimeError(
                "No enterprise WebSocket URL found. Run `python -m copilot login` "
                "with COPILOT_URL=https://m365.cloud.microsoft to sign in and "
                "capture the enterprise chat endpoint."
            )
        if not access_token:
            raise RuntimeError(
                "No access token found. Run `python -m copilot login` to sign in."
            )

        # Build the full WS URL with session params and the access token.
        session_id = uuid.uuid4()
        ws_full = (
            f"{ws_url}"
            f"?chatsessionid={session_id.hex}"
            f"&XRoutingParameterSessionKey={session_id.hex}"
            f"&clientrequestid={session_id.hex}"
            f"&X-SessionId={session_id}"
            f"&ConversationId={conversation_id or session_id}"
            f"&access_token={quote(access_token)}"
            f"&variants={_VARIANTS}"
            f"&source=%22officeweb%22&product=Office&agentHost=Bizchat.FullScreen"
            f"&licenseType=Starter&isEdu=false&agent=web"
            f"&scenario=OfficeWebIncludedCopilot"
        )

        with Session(
            timeout=timeout,
            proxy=proxy,
            impersonate=IMPERSONATE_TARGET,
            headers={"User-Agent": CHROME_UA, **CHROME_CLIENT_HINTS},
            cookies=cookies,
        ) as session:
            # Load the page to establish cookies (enterprise doesn't use
            # Cloudflare, but cookies are still needed for auth).
            session.get("https://m365.cloud.microsoft/chat/")

            wss = session.ws_connect(ws_full)

            # 1. Handshake: send protocol negotiation with SignalR record
            #    separator (\x1e), receive empty {}\x1e
            wss.send(json.dumps({"protocol": "json", "version": 1}).encode() + b"\x1e", CurlWsFlag.TEXT)
            self._recv_frame(wss, time.time() + 10)  # receive {}\x1e

            # 2. Send a ping to keep the connection alive
            wss.send(json.dumps({"type": 6}).encode() + b"\x1e", CurlWsFlag.TEXT)

            # 3. Build and send the chat message
            request_id = uuid.uuid4().hex
            is_start = conversation_id is None
            chat_frame = self._build_chat_frame(
                prompt, request_id, str(session_id), conversation_id, is_start
            )
            wss.send(json.dumps(chat_frame).encode() + b"\x1e", CurlWsFlag.TEXT)

            # 4. Read the reply stream
            upstream_conv_id = None
            for piece in self._read_stream(wss, timeout):
                if isinstance(piece, Conversation):
                    upstream_conv_id = piece.conversation_id
                    if return_conversation:
                        yield piece
                else:
                    yield piece

            # If we started a new conversation, emit the Conversation marker
            # (the id was captured from the type:2 frame).
            if return_conversation and upstream_conv_id and not conversation_id:
                yield Conversation(upstream_conv_id, session.cookies.jar)

    def _build_chat_frame(
        self, text: str, request_id: str, session_id: str,
        conversation_id: Optional[str], is_start: bool,
    ) -> dict:
        """Build the Sydney ``type:4, target:"chat"`` message."""
        return {
            "arguments": [{
                "source": "officeweb",
                "clientCorrelationId": request_id,
                "sessionId": session_id,
                "optionsSets": _OPTION_SETS,
                "streamingMode": "ConciseWithPadding",
                "options": {},
                "extraExtensionParameters": {},
                "allowedMessageTypes": _ALLOWED_MESSAGE_TYPES,
                "sliceIds": [],
                "threadLevelGptId": {},
                "traceId": request_id,
                "isStartOfSession": is_start,
                "clientInfo": {
                    "clientPlatform": "mcmcopilot-web",
                    "clientAppName": "Office",
                    "clientEntrypoint": "mcmcopilot-officeweb",
                    "clientSessionId": session_id,
                    "ProductCategory": "Chat",
                    "clientAppType": "Web",
                    "productEntryPoint": "ChatPanel",
                    "deviceOS": "Windows",
                    "deviceType": "Desktop",
                    "clientPlatformVersion": "10",
                },
                "message": {
                    "author": "user",
                    "inputMethod": "Keyboard",
                    "text": text,
                    "entityAnnotationTypes": [
                        "People", "File", "Event", "Email", "TeamsMessage",
                    ],
                    "requestId": request_id,
                    "locationInfo": {
                        "timeZoneOffset": 8,
                        "timeZone": "Asia/Singapore",
                    },
                    "locale": "en-US",
                    "market": "en-US",
                    "messageType": "Chat",
                    "experienceType": "Default",
                    "adaptiveCards": [],
                    "clientPreferences": {},
                    "connectedFederatedConnections": ["dummyId"],
                },
                "plugins": [{"Id": "BingWebSearch", "Source": "BuiltIn"}],
                "isSbsSupported": True,
                "tone": "Magic",
                "renderReferencesBehindEOS": True,
                "disconnectBehavior": "continue",
                **({"conversationId": conversation_id} if conversation_id else {}),
            }],
            "invocationId": "0",
            "target": "chat",
            "type": 4,
        }

    def _read_stream(self, wss, timeout: int, idle_timeout: int = 60) -> Generator:
        """Consume Sydney WebSocket frames, yielding text chunks.

        Frame types:
          * ``type:1, target:"update"`` — streaming update; text arrives via
            ``writeAtCursor`` (incremental) or ``messages[].text`` (full replace).
          * ``type:2`` — final item with the complete conversation; contains
            ``conversationId``.
          * ``type:3`` — invocation complete (end of turn).
          * ``type:6`` — ping (we should respond with a pong).
        """
        buffer = b""
        last_text = ""
        last_msg = None
        overall_deadline = time.time() + timeout

        while True:
            idle_deadline = time.time() + idle_timeout
            try:
                chunk = self._recv_frame(wss, min(overall_deadline, idle_deadline))
            except Exception:
                break
            if chunk is None:
                if time.time() >= overall_deadline:
                    raise TimeoutError(f"Copilot stream exceeded {timeout}s")
                raise TimeoutError(
                    f"Copilot chat socket went silent for {idle_timeout}s; "
                    f"last frame was {last_msg!r}."
                )

            buffer += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode()
            # SignalR frames are separated by \x1e (record separator). Strip
            # them so drain_json can parse each message cleanly.
            buffer = buffer.replace(b"\x1e", b"")
            messages, buffer = drain_json(buffer)
            for msg in messages:
                last_msg = msg
                msg_type = msg.get("type")

                if msg_type == 1:
                    # Update frame: extract text
                    args = msg.get("arguments", [])
                    if not args:
                        continue
                    arg = args[0]

                    # Incremental text via writeAtCursor
                    if "writeAtCursor" in arg:
                        yield arg["writeAtCursor"]
                        last_text += arg["writeAtCursor"]

                    # Full text replace via messages
                    elif "messages" in arg:
                        for m in arg["messages"]:
                            if m.get("author") == "bot" and "text" in m:
                                full_text = m["text"]
                                if full_text and full_text != last_text:
                                    # Yield only the new part
                                    if full_text.startswith(last_text):
                                        delta = full_text[len(last_text):]
                                        if delta:
                                            yield delta
                                    else:
                                        # Text was replaced entirely; yield full
                                        yield full_text
                                    last_text = full_text

                elif msg_type == 2:
                    # Final item: contains conversationId and complete result
                    item = msg.get("item", {})
                    conv_id = item.get("conversationId")
                    if conv_id:
                        yield Conversation(conv_id, None)

                    # Also yield the final text if we haven't seen it yet
                    result = item.get("result", {})
                    if result.get("value") == "Success":
                        final_text = result.get("message", "")
                        if final_text and final_text != last_text:
                            if final_text.startswith(last_text):
                                delta = final_text[len(last_text):]
                                if delta:
                                    yield delta
                            else:
                                yield final_text

                elif msg_type == 3:
                    # Invocation complete — end of turn
                    return

                elif msg_type == 6:
                    # Ping — respond with pong
                    try:
                        wss.send(json.dumps({"type": 6}).encode() + b"\x1e", CurlWsFlag.TEXT)
                    except Exception:
                        pass

    @staticmethod
    def _recv_frame(wss, deadline: float):
        """Block for one complete WS frame, or return ``None`` past ``deadline``.

        Reassembles libcurl's fragments like ``curl_cffi``'s own ``recv()`` but
        breaks out of the ``CURLE_AGAIN`` wait once ``deadline`` (epoch seconds)
        is reached, so an idle socket can't hang us indefinitely. Non-AGAIN curl
        errors (e.g. a closed connection) propagate to the caller.
        """
        sock_fd = wss.curl.getinfo(CurlInfo.ACTIVESOCKET)
        if sock_fd == _CURL_SOCKET_BAD:
            raise ConnectionError("WebSocket has no active socket")
        chunks = []
        while True:
            try:
                result = wss.recv_fragment()
                # curl_cffi returns (bytes, WsFrameMeta); handle both shapes
                if isinstance(result, tuple):
                    chunk, frame = result
                else:
                    chunk, frame = result, None
                if chunk:
                    chunks.append(chunk)
                if frame is not None and frame.bytesleft == 0 and frame.flags & CurlWsFlag.CONT == 0:
                    return b"".join(chunks)
                elif frame is None and chunk:
                    return chunk
            except CurlError as e:
                if e.code != CurlECode.AGAIN:
                    raise
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                select([sock_fd], [], [], min(0.5, remaining))

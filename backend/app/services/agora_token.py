"""Agora token minting. Every token the system uses is built here, and the only
caller is POST /session/start (api/session.py), which mints all three at once for
one freshly created channel.

Tokens are signed with the App Certificate, which is why this is server-side: the
certificate never reaches the app. The client receives finished tokens at runtime
and holds no Agora credentials of its own.
"""

from app.config import settings
from app.services.agora_access_token.access_token2 import AccessToken, ServiceRtc, ServiceRtm
from app.services.agora_access_token.rtm_token_builder import RtmTokenBuilder

# Raise it here if interviews ever get longer than an hour.
TOKEN_EXPIRY_SECONDS = 3600


def build_rtc_token(channel_name: str, uid: int) -> str:
    """The candidate's RTC token. Sent to the app, used for engine.joinChannel().

    Bound to this channel and this uid, so it is useless anywhere else. No video
    privilege — this is a voice product. PublishDataStream is a fallback only:
    transcripts currently arrive over RTM (agora_agent.py sets data_channel="rtm").
    """
    token = AccessToken(settings.agora_app_id, settings.agora_app_certificate, expire=TOKEN_EXPIRY_SECONDS)

    rtc_service = ServiceRtc(channel_name, uid)
    rtc_service.add_privilege(ServiceRtc.kPrivilegeJoinChannel, TOKEN_EXPIRY_SECONDS)
    rtc_service.add_privilege(ServiceRtc.kPrivilegePublishAudioStream, TOKEN_EXPIRY_SECONDS)
    rtc_service.add_privilege(ServiceRtc.kPrivilegePublishDataStream, TOKEN_EXPIRY_SECONDS)
    token.add_service(rtc_service)

    return token.build()


def build_agent_rtc_rtm_token(channel_name: str, uid: int, rtm_uid: str) -> str:
    """The Conversational AI agent's token. Handed to Agora's REST API in the join
    payload's `token` field; it never goes to the client.

    Both services must be packed into one AccessToken2: with advanced_features.
    enable_rtm set, the agent reuses this same token to log in to RTM rather than
    accepting a second one, and RTM login fails if the token carries only RTC.

    Note uid=0 means "any uid" to Agora, not "uid zero" — ServiceRtc encodes it as
    an empty string, making the RTC half a channel-wide wildcard. That is upstream
    behaviour and fine here because this token stays server-side, but it is why
    this one must never be returned to a client.
    """
    token = AccessToken(settings.agora_app_id, settings.agora_app_certificate, expire=TOKEN_EXPIRY_SECONDS)

    rtc_service = ServiceRtc(channel_name, uid)
    rtc_service.add_privilege(ServiceRtc.kPrivilegeJoinChannel, TOKEN_EXPIRY_SECONDS)
    rtc_service.add_privilege(ServiceRtc.kPrivilegePublishAudioStream, TOKEN_EXPIRY_SECONDS)
    rtc_service.add_privilege(ServiceRtc.kPrivilegePublishDataStream, TOKEN_EXPIRY_SECONDS)
    token.add_service(rtc_service)

    rtm_service = ServiceRtm(rtm_uid)
    rtm_service.add_privilege(ServiceRtm.kPrivilegeLogin, TOKEN_EXPIRY_SECONDS)
    token.add_service(rtm_service)

    return token.build()


def build_rtm_token(user_id: str) -> str:
    """The candidate's RTM token, for receiving transcript and agent-state events.
    Sent to the app, used for rtm.login().

    user_id must match the id the client logs in with exactly — it is compared as a
    string, so "1001" and 1001 are not interchangeable. RTM SDK 2.x also requires
    the AccessToken2/007 format; the legacy 006 builders will not work.
    """
    return RtmTokenBuilder.build_token(
        settings.agora_app_id,
        settings.agora_app_certificate,
        user_id,
        TOKEN_EXPIRY_SECONDS,
    )

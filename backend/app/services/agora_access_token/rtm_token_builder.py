# Vendored from AgoraIO/Tools @ master, DynamicKey/AgoraDynamicKey/python3/src/RtmTokenBuilder2.py
# See packer.py for why this is vendored rather than pip-installed.
#
# Modified from upstream: the wildcard import narrowed to the
# two names actually used, and build_token_with_permissions() dropped — that mints
# an RTM2 token (service type 8) for resource-level permissions we don't use.
__copyright__ = "Copyright (c) 2014-2017 Agora.io, Inc."

from .access_token2 import AccessToken, ServiceRtm


class RtmTokenBuilder:
    @staticmethod
    def build_token(app_id, app_certificate, user_id, expire):
        token = AccessToken(app_id, app_certificate, expire=expire)

        rtm_service = ServiceRtm(user_id)
        rtm_service.add_privilege(ServiceRtm.kPrivilegeLogin, expire)

        token.add_service(rtm_service)

        return token.build()

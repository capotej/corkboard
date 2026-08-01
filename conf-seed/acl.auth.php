# acl.auth.php
# Don't modify the lines above
#
# Closed-wiki defaults baked into the image.
# Anonymous visitors (@ALL) get nothing; logged-in users (@user) get full
# read + write + upload (8 = upload, which is cumulative: read+edit+create+upload).
# Change to 4 (create) instead of 8 if members should NOT upload media.
#
# The bootstrapped 'agent' user (groups user,api) gets DELETE (16) on top of
# @user's 8. ACL takes the highest permission across a user's matched groups,
# so the agent resolves to 16 while ordinary @user members stay at 8. Required
# so the move plugin's media moves pass checkMedia (AUTH_DELETE) without
# elevation code; deletion stays attic-recoverable (pages always; media iff
# $conf['mediarevisions'], locked on in local.protected.php).
# See rfcs/2026-08-01_move-plugin-rpc.md.
#
# none   0
# read   1
# edit   2
# create 4
# upload 8
# delete 16
*               @ALL        0
*               @user       8
*               @api        16

# acl.auth.php
# Don't modify the lines above
#
# Closed-wiki defaults baked into the image.
# Anonymous visitors (@ALL) get nothing; logged-in users (@user) get full
# read + write + upload (8 = upload, which is cumulative: read+edit+create+upload).
# Change to 4 (create) instead of 8 if members should NOT upload media.
#
# none   0
# read   1
# edit   2
# create 4
# upload 8
*               @ALL        0
*               @user       8

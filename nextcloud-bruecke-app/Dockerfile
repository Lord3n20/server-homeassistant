FROM alpine:3.22

RUN apk add --no-cache bash jq rclone fuse3 samba-server samba-common-tools

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]

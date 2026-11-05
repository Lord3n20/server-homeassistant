FROM alpine:3.22

RUN apk add --no-cache bash coreutils curl jq rclone tar

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]

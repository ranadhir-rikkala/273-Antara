FROM alpine:3.18.2 AS fetch
RUN arch=$(uname -m | sed s/aarch64/arm64/ | sed s/x86_64/amd64/) \
 && case "$arch" in \
      arm64) sum=e5605ebffa82a450ebbcdf6cf19dad546e1e40d52dbf3e03cfd2d2d5b7394211 ;; \
      amd64) sum=33997073eb6d82b7be4f27f9fc8ec9e28a395e0f20674d5d54bb2fa28a75488d ;; \
      *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac \
 && wget -q -O /tmp/hq.tar.gz "https://github.com/SigNoz/signoz/releases/download/histogram-quantile%2Fv0.0.1/histogram-quantile_linux_${arch}.tar.gz" \
 && echo "$sum  /tmp/hq.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/hq.tar.gz -C /tmp histogram-quantile \
 && chmod +x /tmp/histogram-quantile

FROM alpine:3.18.2
COPY --from=fetch /tmp/histogram-quantile /histogram-quantile

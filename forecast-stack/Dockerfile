FROM node:22.22.0-bookworm-slim
WORKDIR /home/node/app
COPY --chown=node:node forecast-core/src/lib/ ./forecast-core/src/lib/
COPY --chown=node:node space/ ./space/
USER node
ENV NODE_ENV=production PORT=7860
EXPOSE 7860
CMD ["node", "--experimental-strip-types", "space/server.mts"]

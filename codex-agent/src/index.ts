import Fastify from "fastify";
import { ZodError } from "zod";
import { config } from "./config.js";
import { registerRoutes } from "./routes.js";

const app = Fastify({
  logger: true
});

app.setErrorHandler((error, request, reply) => {
  if (error instanceof ZodError) {
    return reply.code(400).send({
      error: "Invalid request",
      issues: error.issues
    });
  }

  const err = error as Error & { statusCode?: number };
  const statusCode = typeof err.statusCode === "number" ? err.statusCode : 500;
  request.log.error(error);

  return reply.code(statusCode).send({
    error: statusCode >= 500 ? "Internal server error" : err.message
  });
});

await registerRoutes(app);

await app.listen({
  port: config.PORT,
  host: "0.0.0.0"
});

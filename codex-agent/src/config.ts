import "dotenv/config";
import { z } from "zod";

const ConfigSchema = z.object({
  PORT: z.coerce.number().int().positive().default(3001),
  DATABASE_URL: z.string().default("./data/qa-agent.sqlite"),
  LLM_MOCK: z
    .enum(["true", "false"])
    .default("false")
    .transform((value) => value === "true"),
  LLM_BASE_URL: z.string().url().default("https://sub2api.agoralab.co"),
  LLM_API_KEY: z.string().optional(),
  LLM_MODEL: z.string().default("gpt-5.5"),
  LLM_REASONING_EFFORT: z.enum(["low", "medium", "high"]).default("high")
}).superRefine((value, ctx) => {
  if (!value.LLM_MOCK && (!value.LLM_API_KEY || value.LLM_API_KEY.length === 0)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["LLM_API_KEY"],
      message: "LLM_API_KEY is required unless LLM_MOCK=true"
    });
  }
});

export const config = ConfigSchema.parse(process.env);

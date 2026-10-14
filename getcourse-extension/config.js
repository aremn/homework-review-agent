"use strict";

const SUPABASE_URL = process.env.SUPABASE_URL;

if (!SUPABASE_URL || !URL.canParse(SUPABASE_URL)) {
  throw new Error("Set SUPABASE_URL when building the extension.");
}

module.exports = { SUPABASE_URL };

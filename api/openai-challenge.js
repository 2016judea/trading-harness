// GET /.well-known/openai-apps-challenge (vercel.json rewrites it here): the domain token the
// ChatGPT plugin portal asks for. Set it as Vercel env OPENAI_APPS_CHALLENGE and redeploy; 404 until set.
// Leave it set after approval: OpenAI re-checks it (heistclips SUBMISSION.md).
module.exports = (req, res) => {
  const t = (process.env.OPENAI_APPS_CHALLENGE || "").trim();
  res.statusCode = t ? 200 : 404;
  res.setHeader("content-type", "text/plain; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(t || "not set");
};

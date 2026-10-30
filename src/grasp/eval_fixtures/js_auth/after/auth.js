const jwt = require("jsonwebtoken");
const auditLog = require("./audit");

function authenticate(req, res, next) {
  const header = req.headers.authorization || "";
  if (!header.startsWith("Bearer ")) {
    return res.status(401).json({ error: "missing token" });
  }
  const token = header.slice(7);
  try {
    req.user = jwt.verify(token, process.env.JWT_SECRET);
  } catch (err) {
    return res.status(401).json({ error: "invalid token" });
  }
  next();
}

function requireRole(...roles) {
  return (req, res, next) => {
    if (roles.includes(req.user.role)) {
      return next();
    }
    res.status(403).end();
  };
}

module.exports = { authenticate, requireRole };

export function HummingbotLogo({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ display: 'inline-block', verticalAlign: 'middle', marginRight: 8 }}
    >
      <defs>
        <linearGradient id="hbGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#00E5FF" />
          <stop offset="50%" stopColor="#4A9D8A" />
          <stop offset="100%" stopColor="#2ECC71" />
        </linearGradient>
      </defs>
      {/* Hummingbot Faceted Hummingbird Silhouette */}
      <path d="M4 16C4 16 9 6 18 4C27 2 28 8 28 8C28 8 22 12 18 14C14 16 10 17 4 16Z" fill="url(#hbGrad)" opacity="0.95" />
      <path d="M18 14C22 12 28 8 28 8C28 8 26 18 20 22C14 26 10 24 10 24C10 24 14 18 18 14Z" fill="url(#hbGrad)" opacity="0.8" />
      <path d="M10 24C10 24 16 28 22 27C28 26 30 22 30 22C30 22 24 24 18 22C12 20 10 24 10 24Z" fill="url(#hbGrad)" opacity="0.6" />
      <circle cx="24" cy="8" r="1.5" fill="#FFFFFF" />
    </svg>
  )
}

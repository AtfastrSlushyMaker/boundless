export function AtlasArtwork({ compact = false }: { compact?: boolean }) {
  return (
    <svg className={`atlas-art${compact ? " atlas-art--compact" : ""}`} viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" role="img" aria-label="An old cartographer's map of a fictional coast">
      <defs>
        <pattern id="grain" width="180" height="180" patternUnits="userSpaceOnUse">
          <circle cx="12" cy="30" r="1" /><circle cx="61" cy="108" r=".7" /><circle cx="129" cy="44" r=".8" />
          <circle cx="154" cy="155" r=".6" /><circle cx="91" cy="167" r=".5" /><circle cx="35" cy="74" r=".6" />
        </pattern>
      </defs>
      <rect width="1440" height="900" fill="url(#grain)" className="map-grain" />
      <g className="map-coast" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
        <path className="coast-main" d="M805 72c-23 35-57 38-73 67-18 34 6 59-13 93-22 40-66 41-82 80-16 38 17 70-7 104-23 31-61 27-85 57-27 33-13 72-37 101-22 27-57 23-76 54-21 34-4 75-31 103-24 26-65 16-89 46-24 31-12 67-42 91-24 18-52 10-76 29-17 13-26 34-37 57 61 11 107-5 149-31 47-29 55-64 103-79 39-12 69 2 106-19 42-24 46-68 91-90 45-22 79 0 119-28 40-28 35-74 77-104 40-29 79-14 116-47 38-33 27-80 67-111 34-26 69-14 103-41 43-35 37-94 80-123 43-28 80-4 112-31 23-19 32-54 35-89-40-28-76-40-113-34-40 7-57 33-98 29-37-4-53-28-91-25-41 3-59 35-99 37-39 2-55-24-93-18-41 6-59 42-99 50-38 8-65-13-102 1-33 13-45 46-82 57-34 10-61-4-92 14-25 14-36 41-55 64-20 25-49 42-76 55" />
        <path d="M765 140c-22 39-63 48-78 81-14 31 7 54-10 83-20 33-58 36-72 68-14 31 10 57-10 84-20 27-54 27-75 55-22 30-13 61-34 87-20 25-51 26-67 54-17 29-9 59-27 84" />
        <path d="M713 198c-19 32-49 40-64 68-14 27 4 46-10 70-16 27-48 30-60 56-12 26 7 47-10 70-16 23-46 26-62 49-16 25-12 49-29 70-16 20-42 24-56 46-13 21-12 42-23 62" />
        <path d="M880 221c43-22 76-30 110-20 40 12 52 43 91 46 38 3 61-23 98-13 29 8 43 32 65 47" />
        <path d="M918 270c34-15 62-20 88-12 33 11 42 34 73 37 30 3 50-16 80-9 23 5 35 23 51 36" />
        <path d="M1075 550c-33 20-48 53-84 64-35 12-58-7-92 10-36 18-44 52-81 67-36 14-60-1-93 19-27 16-39 40-58 65" />
        <path d="M1120 602c-32 17-47 45-79 55-30 10-50-7-78 7-31 15-38 44-69 57-30 13-51 0-80 17-22 12-34 31-49 49" />
        <path d="M983 723c38-27 72-34 106-24 32 9 43 32 74 33 29 1 47-19 77-10 18 5 33 17 47 31" />
        <path d="M250 765c49-21 79-23 109-12 31 12 44 34 76 37 28 3 48-12 77-5" />
      </g>
      <g className="map-routes" fill="none" stroke="currentColor" strokeLinecap="round">
        <path d="M300 755C486 649 605 578 771 518s286-122 456-298" />
        <path d="M402 803c168-98 284-154 422-199" />
      </g>
      <g className="map-mountains" fill="none" stroke="currentColor" strokeLinejoin="round">
        <path d="m704 373 20-35 19 35m-6-5 23-41 24 41m-5-3 22-37 22 39m-6 3 17-29 16 28" />
        <path d="m963 477 17-30 17 30m-4-3 20-34 19 35m-3 1 15-27 16 27" />
        <path d="m547 616 18-31 18 31m-3-3 20-33 20 34m-1 0 14-24 14 24" />
      </g>
      <g className="map-labels">
        <text x="846" y="377" className="map-title">VAELORIA</text>
        <text x="980" y="219" className="map-label">THE ASHEN REACH</text>
        <text x="433" y="711" className="map-label">MOURNING COAST</text>
        <text x="1050" y="660" className="map-label">BLACKWATER SOUND</text>
        <text x="238" y="386" className="map-label map-label--small">WESTERN SEA</text>
        <text x="725" y="454" className="map-label map-label--small">OLD ROAD</text>
        <text x="1254" y="820" className="map-coordinate">44° N · 18° E</text>
      </g>
      <g className="map-compass" transform="translate(1215 116)">
        <circle r="35" fill="none" stroke="currentColor" />
        <path d="M0-28 5-4 26 0 5 4 0 28-5 4-26 0-5-4Z" fill="currentColor" />
        <text x="-3" y="-45">N</text>
      </g>
    </svg>
  );
}

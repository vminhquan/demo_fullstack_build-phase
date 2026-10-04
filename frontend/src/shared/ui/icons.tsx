import { SVGProps } from "react";

const paths = {
  logo: <path d="M8 1.5 15 14H1L8 1.5Z" fill="currentColor" stroke="none" />,
  power: <><path d="M8 1.75V7.5" /><path d="M4.6 3.9a5.25 5.25 0 1 0 6.8 0" /></>,
  folder: <path d="M1.75 4.25c0-.83.67-1.5 1.5-1.5h3l1.5 1.75h5c.83 0 1.5.67 1.5 1.5v6.25c0 .83-.67 1.5-1.5 1.5h-9.5c-.83 0-1.5-.67-1.5-1.5v-8Z" />,
  grid: <><rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" /><rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" /></>,
  file: <><path d="M9.5 1.75H4.25c-.83 0-1.5.67-1.5 1.5v9.5c0 .83.67 1.5 1.5 1.5h7.5c.83 0 1.5-.67 1.5-1.5V5.5L9.5 1.75Z" /><path d="M9.5 1.75V5.5h3.75M5.5 8.75h5M5.5 11.25h3.5" /></>,
  check: <><circle cx="8" cy="8" r="6.25" /><path d="m5.5 8.25 1.75 1.75 3.25-3.75" /></>,
  layers: <><path d="M8 1.75 14.25 5 8 8.25 1.75 5 8 1.75Z" /><path d="m1.75 8 6.25 3.25L14.25 8M1.75 11 8 14.25 14.25 11" /></>,
  activity: <path d="M1.75 8h2.5l2-5 3.5 10 2-5h2.5" />,
  users: <><circle cx="6" cy="5.25" r="2.5" /><path d="M1.75 13.5c0-2.35 1.9-4.25 4.25-4.25s4.25 1.9 4.25 4.25M10.5 2.9a2.5 2.5 0 0 1 0 4.7M12 9.5c1.3.6 2.25 2.02 2.25 4" /></>,
  clock: <><circle cx="8" cy="8" r="6.25" /><path d="M8 4.5V8l2.5 1.5" /></>,
  search: <><circle cx="7" cy="7" r="4.75" /><path d="m10.5 10.5 3.75 3.75" /></>,
  logout: <path d="M6 14.25H3.25c-.83 0-1.5-.67-1.5-1.5v-9.5c0-.83.67-1.5 1.5-1.5H6M10.5 11.25 13.75 8 10.5 4.75M13.5 8H6" />,
  plus: <path d="M8 2.75v10.5M2.75 8h10.5" />,
  chevrons: <path d="m5 6 3-3 3 3M5 10l3 3 3-3" />,
  chevronDown: <path d="m4 6 4 4 4-4" />,
  arrowLeft: <path d="M13.25 8H2.75M7 3.75 2.75 8 7 12.25" />,
  x: <path d="m4 4 8 8M12 4l-8 8" />,
  tick: <path d="m3.25 8.5 3 3 6.5-7" />,
  arrowUp: <path d="M8 13.25V2.75M3.75 7 8 2.75 12.25 7" />,
  sliders: <path d="M2.75 4.5h6.5M12.25 4.5h1M2.75 11.5h1M6.75 11.5h6.5M10.75 3v3M5.25 10v3" />,
  mail: <><rect x="1.75" y="3.25" width="12.5" height="9.5" rx="1.5" /><path d="m2.25 4 5.75 4.5L13.75 4" /></>,
  arrowRight: <path d="M2.75 8h10.5M9 3.75 13.25 8 9 12.25" />,
  settings: <><circle cx="8" cy="8" r="2.25" /><path d="M8 1.75v1.5M8 12.75v1.5M3.58 3.58l1.06 1.06M11.36 11.36l1.06 1.06M1.75 8h1.5M12.75 8h1.5M3.58 12.42l1.06-1.06M11.36 4.64l1.06-1.06" /></>,
  more: <><circle cx="3.5" cy="8" r="1" fill="currentColor" stroke="none" /><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none" /><circle cx="12.5" cy="8" r="1" fill="currentColor" stroke="none" /></>,
  edit: <path d="M10.75 2.75 13.25 5.25 5.5 13H3v-2.5l7.75-7.75ZM9.25 4.25l2.5 2.5" />,
  trash: <path d="M2.75 4.25h10.5M6.25 4.25V2.75h3.5v1.5M4 4.25l.6 8.6c.06.8.72 1.4 1.5 1.4h3.8c.78 0 1.44-.6 1.5-1.4l.6-8.6" />,
  refresh: <><path d="M13.25 3.5v3.25H10" /><path d="M12.9 6.5A5.25 5.25 0 1 0 13 10" /></>,
};

export type IconName = keyof typeof paths;

export function Icon({ name, size = 16, ...props }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}

import { deleteAllUsers } from "$lib/server/secrets";

export function GET() {
  return new Response("ok");
}

export async function POST() {
  deleteAllUsers();
  return new Response("done");
}

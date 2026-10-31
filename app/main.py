            proposal_by_id = {proposal["packageId"]: proposal for proposal in proposals}
            seen = set()
            executions = []
            for result in result_data.get("results", []):
                proposal = proposal_by_id.get(result.get("packageId"))
                if (
                    not proposal
                    or proposal["packageId"] in seen
                    or result.get("actionId") != proposal["actionId"]
                    or result.get("action") != proposal["action"]
                    or result.get("outcome") not in ("ACCEPTED", "REJECTED")
                    or not isinstance(result.get("receiptNonce"), str)
                    or not result["receiptNonce"]
                ):
                    connection.rollback()
                    connection.close()
                    raise HTTPException(400, "INVALID_RESULT")
                seen.add(proposal["packageId"])
                if result["outcome"] == "ACCEPTED":
                    executions.append(
                        {
                            "packageId": proposal["packageId"],
                            "actionId": proposal["actionId"],
                            "action": proposal["action"],
                            "receiptNonce": result["receiptNonce"],
                            "facts": proposal["facts"],
                            "evidenceRefs": proposal["evidenceRefs"],
                        }
                    )

            if len(seen) != len(proposals):
                connection.rollback()
                connection.close()
                raise HTTPException(400, "INCOMPLETE_RESULTS")

            task["history"].append(message)
            task["artifacts"].append(
                {
                    "name": "Invoice action receipts",
                    "parts": [
                        {
                            "mediaType": RECEIPT,
                            "data": {
                                "batchId": row["batch_id"],
                                "executions": executions,
                            },
                        }
                    ],
                }
            )
            task["status"] = {"state": "TASK_STATE_COMPLETED"}
            update = connection.execute(
                "UPDATE tasks SET state='TASK_STATE_COMPLETED',task_json=?,updated_at=? "
                "WHERE id=? AND principal=? AND state='TASK_STATE_INPUT_REQUIRED'",
                (canonical(task), stamp(), task_id, owner),
            )
            if update.rowcount != 1:
                connection.rollback()
                connection.close()
                raise HTTPException(409, "TASK_TERMINAL")

        connection.execute(
            "INSERT INTO messages VALUES(?,?,?,?)",
            (owner, message_id, message_digest, task_id),
        )
        connection.commit()
        connection.close()
        return a2a_response({"task": task})


@app.get(PREFIX + "/tasks/{task_id}")
async def get_task(
    task_id: str, authorization: str | None = Header(default=None)
):
    owner = principal(authorization)
    connection = connect()
    row = task_row(connection, task_id, owner)
    task = json.loads(row["task_json"])
    connection.close()
    return a2a_response(task)


@app.get(PREFIX + "/tasks")
async def list_tasks(authorization: str | None = Header(default=None)):
    owner = principal(authorization)
    connection = connect()
    rows = connection.execute(
        "SELECT task_json FROM tasks WHERE principal=? ORDER BY created_at DESC",
        (owner,),
    ).fetchall()
    connection.close()
    return a2a_response({"tasks": [json.loads(row["task_json"]) for row in rows]})


@app.post(PREFIX + "/tasks/{task_id}:cancel")
async def cancel_task(
    task_id: str, authorization: str | None = Header(default=None)
):
    owner = principal(authorization)
    with LOCK:
        connection = connect()
        connection.execute("BEGIN IMMEDIATE")
        row = task_row(connection, task_id, owner, lock=True)
        if row["state"] != "TASK_STATE_INPUT_REQUIRED":
            connection.rollback()
            connection.close()
            raise HTTPException(409, "TASK_TERMINAL")

        task = json.loads(row["task_json"])
        task["status"] = {"state": "TASK_STATE_CANCELED"}
        update = connection.execute(
            "UPDATE tasks SET state='TASK_STATE_CANCELED',task_json=?,updated_at=? "
            "WHERE id=? AND principal=? AND state='TASK_STATE_INPUT_REQUIRED'",
            (canonical(task), stamp(), task_id, owner),
        )
        if update.rowcount != 1:
            connection.rollback()
            connection.close()
            raise HTTPException(409, "TASK_TERMINAL")
        connection.commit()
        connection.close()
        return a2a_response(task)


@app.get("/health")
async def health():
    # Render health checks do not need the A2A authentication contract.
    return {"status": "ok"}

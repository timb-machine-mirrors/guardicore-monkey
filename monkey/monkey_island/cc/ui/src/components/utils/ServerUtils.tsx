import IslandHttpClient, {APIEndpoint} from '../IslandHttpClient';



export function doesAnyAgentExist() {
  return _getAllAgents().then(all_agents => {
    if (all_agents.length > 0) {return true;}
    return false;
    })
}

export function didAllAgentsShutdown() {
  return _getAllAgents().then(all_agents => {
    for (let idx in all_agents) {
      let agent = all_agents[idx];
      if (agent.stop_time === null) {return false;}
    }
    return true;
  })
}

export function getCollectionObject(collectionEndpoint: APIEndpoint, key: string) {
  return IslandHttpClient.get(collectionEndpoint)
      .then(res => {
        return _arrayToObject(res.body, key);
      })
}

function _arrayToObject(array: object[], key: string){
  return array.reduce((prev, curr) => ({...prev, [curr[key]]: curr}), {});
}

function _getAllAgents() {
    return IslandHttpClient.get(APIEndpoint.agents)
    .then(res => {
        return res.body;
    });
}
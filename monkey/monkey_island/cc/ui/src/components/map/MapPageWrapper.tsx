import React, {useEffect, useState} from 'react';
import IslandHttpClient, {APIEndpoint} from '../IslandHttpClient';
import {arrayToObject, getCollectionObject} from '../utils/ServerUtils';
import MapPage from '../pages/MapPage';
import MapNode, {
  Agent,
  CommunicationType,
  Communications,
  getMachineIp,
  Machine,
  Node
} from '../types/MapNode';
import _ from 'lodash';
import generateGraph, {Graph} from './GraphCreator';

const MapPageWrapper = (props) => {
  function getPropagationEvents() {
    let url_args = {'type': 'PropagationEvent', 'success': true};
    return IslandHttpClient.get(APIEndpoint.agentEvents, url_args)
      .then(res => arrayToObject(res.body, 'target'));
  }

  const [mapNodes, setMapNodes] = useState<MapNode[]>([]);
  const [nodes, setNodes] = useState<Record<string, Node>>({});
  const [machines, setMachines] = useState<Record<string, Machine>>({});
  const [agents, setAgents] = useState<Record<string, Agent>>({});
  const [propagationEvents, setPropagationEvents] = useState({});

  const [graph, setGraph] = useState<Graph>({edges: [], nodes: []});
  // We need to avoid re-drawing the map, but the original data gets modified
  // by the "react-graph-vis". This snapshot stores unmodified data for comparison
  const [graphSnapshot, setGraphSnapshot] = useState<Graph>({edges: [], nodes: []});


  function fetchMapNodes() {
    getCollectionObject(APIEndpoint.nodes, 'machine_id').then(nodeObj => setNodes(nodeObj));
    getCollectionObject(APIEndpoint.machines, 'id').then(machineObj => setMachines(machineObj));
    getCollectionObject(APIEndpoint.agents, 'machine_id').then(agentObj => setAgents(agentObj));
    getPropagationEvents().then(events => setPropagationEvents(events));
  }

  useEffect(() => {
    fetchMapNodes();
    let oneSecond = 1000;
    const interval = setInterval(() => {
      fetchMapNodes();
    }, oneSecond);

    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    let localGraph = generateGraph(mapNodes);
    if (mapNodes.length !== 0 && ! _.isEqual(localGraph, graphSnapshot)) {
      setGraphSnapshot(_.cloneDeep(localGraph));
      setGraph(localGraph);
    }
  }, [mapNodes]);


  useEffect(() => {
    setMapNodes(buildMapNodes());
  }, [nodes, machines, propagationEvents]);

  function addRelayCommunications(communications: Communications) {
    for (let [machineId, commTypes] of Object.entries(communications)) {
      let machine = machines[machineId];
      if (machine !== undefined && !machine.island
        && commTypes.includes(CommunicationType.cc)
        && !commTypes.includes(CommunicationType.relay)) {
        commTypes.push(CommunicationType.relay);
      }
    }
  }

  function buildMapNodes(): MapNode[] {
    // Build the MapNodes list
    let mapNodes: MapNode[] = [];
    for (const machine of Object.values(machines)) {
      let node = nodes[machine.id] || null;
      let communications;
      if (node !== null) {
        communications = node.connections;
        addRelayCommunications(communications);
      } else {
        communications = [];
      }
      let running = false;
      let agentID: string | null = null;
      let parentID: string | null = null;
      let agentStartTime: Date = new Date(0);
      if (node !== null && machine.id in agents) {
        let agent = agents[machine.id];
        running = isAgentRunning(agent);
        agentID = agent.id;
        parentID = agent.parent_id;
        agentStartTime = new Date(agent.start_time);
      }

      let propagatedTo = wasMachinePropagated(machine, propagationEvents);

      mapNodes.push(new MapNode(
        machine.id,
        machine.network_interfaces,
        running,
        communications,
        machine.operating_system,
        machine.hostname,
        machine.island,
        propagatedTo,
        agentStartTime,
        agentID,
        parentID
      ));
    }

    return mapNodes;
  }

  function isAgentRunning(agent: Agent): boolean {
    return !Boolean(agent.stop_time)
  }

  function wasMachinePropagated(machine, propagationEvents): boolean {
    let ip = getMachineIp(machine);
    return ip in propagationEvents
  }

  return (<MapPage mapNodes={mapNodes} graph={graph} {...props} />);
}


export default MapPageWrapper